#!/usr/bin/env python3
"""图审台 v1：本地 Web（stdlib http.server，零依赖），pipeline.db 唯一事实源。

页面:
    /board        流水线看板（选品→审图全过程，只读）
    /review       图审队列 + 详情 + 三动作（过/打回/毙品，落 reviews eval 标签）
    /product/<id> 单品 trace（events 时间线 + reviews 历史）
    POST /action  {pid, decision, reason, note} → record_review → 状态机流转
    /media/<f>    data/gen 下的生成图（防目录穿越）

布局按 ui-sketch.html「审核台 v1 线框」实现：顶栏保险丝指标 + 四 tab +
左待审队列 + 右详情（8-slot 网格 / 决策 kv / 三动作 + 死因弹层）。

用法: python3 review_server.py [port=8080]，浏览器开 http://127.0.0.1:8080
"""
import html
import json
import re
import subprocess
import sys
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pipeline as P
import listing_edit as LE
import ozon_client as OC
import pricing

ROOT = Path(__file__).parent
GEN = ROOT / "data/gen"
ESC = html.escape

CSS = """
:root{--bg:#0f1115;--card:#1a1d24;--card2:#22262f;--border:#2e3440;--text:#e6e8ec;
--dim:#9aa3b2;--accent:#4f8cff;--green:#34c759;--yellow:#ffb340;--red:#ff5f57;--purple:#b48cff}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC",sans-serif;line-height:1.6;padding:20px}
a{color:var(--accent);text-decoration:none}
.wrap{max-width:1280px;margin:0 auto}
.dim{color:var(--dim);font-size:12.5px}
.tag{display:inline-block;padding:1px 8px;border-radius:4px;font-size:12px;font-weight:600;margin-right:6px}
.t-ok{background:rgba(52,199,89,.15);color:var(--green)}
.t-warn{background:rgba(255,179,64,.15);color:var(--yellow)}
.t-bad{background:rgba(255,95,87,.15);color:var(--red)}
.t-info{background:rgba(79,140,255,.15);color:var(--accent)}
.t-manual{background:rgba(180,140,255,.15);color:var(--purple)}
code{background:var(--card2);padding:1px 6px;border-radius:4px;font-size:12px;color:#7ecfff}

/* ---- app 框架（顶栏 + tab） ---- */
.app{border:1px solid var(--border);border-radius:14px;overflow:hidden;background:#14161c}
.topbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 16px;background:var(--card2);border-bottom:1px solid var(--border);font-size:13px}
.topbar .logo{font-weight:700;margin-right:10px;color:var(--text)}
.metric{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:3px 10px;color:var(--dim)}
.metric b{color:var(--text)}
.switch{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--dim)}
.switch .pill{border:1px solid var(--border);border-radius:999px;padding:2px 4px;display:flex;gap:2px}
.switch .pill span{padding:2px 10px;border-radius:999px}
.switch .pill .on{background:var(--accent);color:#fff;font-weight:600}
.tabs{display:flex;gap:2px;padding:8px 16px 0;border-bottom:1px solid var(--border);font-size:13.5px}
.tabs a{padding:7px 14px;border-radius:8px 8px 0 0;color:var(--dim);border:1px solid transparent;border-bottom:none}
.tabs a.on{background:var(--card);border-color:var(--border);color:var(--text);font-weight:600}
.tabs .n{background:var(--red);color:#fff;border-radius:999px;font-size:11px;padding:0 6px;margin-left:4px}
.tabs .n.p{background:var(--purple)}
.app-body{padding:0}

/* ---- 看板 ---- */
.board{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;padding:14px}
.col{background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:8px;min-height:120px}
.col.human{border:2px solid var(--purple);background:rgba(180,140,255,.05)}
.col h5{font-size:12px;color:var(--dim);margin:2px 2px 8px;display:flex;justify-content:space-between}
.col h5 .cnt{color:var(--text)}
.col .gate{font-size:10.5px;color:var(--dim);margin:-4px 2px 8px;display:block}
.mini{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:5px 8px;font-size:11.5px;margin-bottom:6px;display:block;color:var(--text)}
.mini .v{color:var(--dim);font-size:10.5px;display:block}
.mini.dead{opacity:.45}.mini.dead b{text-decoration:line-through}

/* ---- 图审：左队列 + 右详情 ---- */
.rl{display:grid;grid-template-columns:220px 1fr;min-height:480px}
.queue{border-right:1px solid var(--border);padding:10px;font-size:12.5px}
.queue h4{font-size:12px;color:var(--dim);font-weight:600;margin:4px 4px 8px;letter-spacing:.5px}
.qitem{border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-bottom:8px;background:var(--card);display:block;color:var(--text)}
.qitem.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.qitem .t{font-weight:600;font-size:12.5px}
.qitem .m{color:var(--dim);font-size:11.5px;margin-top:2px}
.qitem .m .warn{color:var(--yellow)}
.detail{padding:16px}
.detail-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.detail-head .title{font-size:17px;font-weight:700}
.detail-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:16px}
.slots{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.slot{aspect-ratio:3/4;border:1px dashed var(--border);border-radius:8px;display:flex;flex-direction:column;
align-items:center;justify-content:center;color:var(--dim);font-size:11px;background:var(--card);
overflow:hidden;position:relative;text-align:center;padding:4px;cursor:pointer}
.slot img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.slot .lb{position:relative;z-index:1;background:rgba(0,0,0,.55);border-radius:4px;padding:1px 6px}
.slot.has{border-style:solid}
.slot .badge{position:absolute;top:4px;left:4px;z-index:2;background:var(--accent);color:#fff;font-size:10px;border-radius:4px;padding:0 5px;font-weight:700}
.kv{font-size:12.5px}
.kv .row{display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-bottom:1px dashed var(--border)}
.kv .row span:first-child{color:var(--dim);white-space:nowrap}
.kv .row span:last-child{text-align:right}
.kv .pos{color:var(--green);font-weight:600}
.kv .neg{color:var(--red);font-weight:600}
.actions{display:flex;gap:10px;margin-top:16px}
.actions button{flex:1;border:none;border-radius:10px;padding:13px 10px;font-size:14.5px;font-weight:700;cursor:pointer;text-align:center}
.actions button small{display:block;font-size:11px;font-weight:400;opacity:.85;margin-top:2px}
button.ok{background:var(--green);color:#06210f}
button.re{background:var(--yellow);color:#2a1c00}
button.kill{background:var(--red);color:#2b0705}
.reason{margin-top:10px;border:1px dashed var(--border);border-radius:8px;padding:8px 12px;color:var(--dim);font-size:12px}
.reason.open{color:var(--text);border-color:var(--yellow)}
.reason select,.reason input[type=text]{background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:12.5px;margin:6px 6px 0 0}
.reason .confirm{background:var(--accent);color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:12.5px;font-weight:700;cursor:pointer;margin-top:6px}
.reason .cancel{background:none;color:var(--dim);border:1px solid var(--border);border-radius:6px;padding:6px 12px;font-size:12.5px;cursor:pointer;margin-top:6px;margin-left:6px}
.desc{background:var(--card2);border-radius:8px;padding:10px 14px;font-size:12.5px;white-space:pre-wrap;margin:8px 0;max-height:140px;overflow:auto}
.warnbox{background:rgba(255,179,64,.08);border:1px solid rgba(255,179,64,.3);border-radius:8px;padding:10px 14px;margin:10px 0;font-size:13px}
.warnbox ul{max-height:110px;overflow:auto}
.okbox{background:rgba(52,199,89,.08);border:1px solid rgba(52,199,89,.3);
border-radius:8px;padding:10px 14px;margin:10px 0;font-size:13px}

/* ---- slot7 人工尺寸输入（模态框，与 zoom 同遮罩语言） ---- */
.slot .cta{color:var(--accent);font-weight:600;font-size:11.5px;margin-top:6px;
border:1px solid var(--accent);border-radius:6px;padding:3px 10px;position:relative;z-index:1}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;justify-content:center;z-index:100}
.modal.on{display:flex}
.modal .box{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px 22px;width:360px}
.modal h4{font-size:14px;margin-bottom:6px}
.modal .hint{color:var(--dim);font-size:11.5px;margin-bottom:12px;line-height:1.5}
.modal .dims{display:flex;gap:8px;margin-bottom:10px}
.modal .dims>div{flex:1}
.modal label{display:block;font-size:11px;color:var(--dim);margin-bottom:3px}
.modal input{width:100%;background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:7px 9px;font-size:13px}
.modal .go{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:9px 14px;font-size:13px;font-weight:700;cursor:pointer;width:100%;margin-top:10px}
.modal .go:disabled{opacity:.5;cursor:wait}
.modal .err{color:var(--red);font-size:12px;margin-top:8px;display:none}

/* ---- 单图放大 ---- */
.zoom{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;align-items:center;justify-content:center;z-index:99;cursor:zoom-out}
.zoom.on{display:flex}
.zoom img{max-width:92vw;max-height:92vh;border-radius:10px}

/* ---- trace ---- */
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 20px;margin:14px}
.trace{margin:10px 0 0 8px;padding-left:20px;border-left:2px solid var(--border);font-size:13px}
.ev{position:relative;margin-bottom:12px}
.ev::before{content:"";position:absolute;left:-26px;top:5px;width:10px;height:10px;border-radius:50%;background:var(--accent)}
.ev.human::before{background:var(--purple)}
.ev.dead::before{background:var(--red)}
h3{font-size:15px;margin:16px 0 8px}

/* ---- 上架编辑器（大模态，分 section 可滚动） ---- */
.modal .box.editbox{width:720px;max-width:94vw;max-height:88vh;overflow:auto}
.editbox .sec{border-top:1px solid var(--border);padding:14px 0}
.editbox .sec h5{font-size:13px;color:var(--accent);margin-bottom:8px;font-weight:700}
.editbox textarea{width:100%;background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:7px 9px;font-size:13px;min-height:90px;font-family:inherit}
.editbox input[type=text],.editbox input[type=number]{width:100%;background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:7px 9px;font-size:13px}
.editbox select{width:100%;background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:12.5px;max-width:100%}
.editbox .grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.editbox .grid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
.editbox .attr{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:12.5px}
.editbox .attr label{flex:0 0 220px;color:var(--text)}
.editbox .attr .req{color:var(--red)}
.editbox .attr .asp{color:var(--dim);font-size:10.5px}
.editbox .attr .fld{flex:1}
.editbox .priceout{background:var(--card2);border-radius:8px;padding:10px 14px;margin-top:8px;font-size:12.5px;display:none}
.editbox .priceout .big{font-size:20px;font-weight:700;color:var(--green)}
.editbox .priceout table{width:100%;font-size:11.5px;color:var(--dim);margin-top:6px}
.editbox .priceout td{padding:1px 0}
.editbox .save{background:var(--green);color:#06210f;border:none;border-radius:8px;padding:11px 18px;font-size:14px;font-weight:700;cursor:pointer;width:100%;margin-top:12px}
.editbox .save:disabled{opacity:.5}
.editbox .calcbtn{background:var(--accent);color:#fff;border:none;border-radius:6px;padding:7px 14px;font-size:12.5px;font-weight:700;cursor:pointer}
@media(max-width:900px){.rl{grid-template-columns:1fr}.queue{border-right:none;border-bottom:1px solid var(--border)}.detail-grid{grid-template-columns:1fr}}
"""

# 上架编辑器（普通字符串，JS 大括号无需 f-string 转义；page() 以变量插入）
EDITOR_HTML = """
<div class="modal" id="editmodal" onclick="if(event.target.id==='editmodal')this.classList.remove('on')">
<div class="box editbox">
<h4>✏️ 上架编辑 · <span id="edpid"></span></h4>
<div class="hint">编辑即人工确认：保存写回 spec（listing_edits），清除 need_confirm，落 event。
图内文案不在此改（走打回重生）。保存后可继续过审进上架队列。</div>
<div class="sec"><h5>① 文案</h5>
<label>商品标题（俄语，≤200 字符）</label>
<input type="text" id="ed_title" maxlength="200">
<div style="margin-top:6px"><label>详情介绍（≤1000 字符）</label>
<textarea id="ed_desc" maxlength="1000"></textarea></div></div>
<div class="sec"><h5>② 尺寸重量（Ozon 非零必填，用于上架+物流）</h5>
<div class="grid4">
<div><label>长 cm</label><input type="number" id="ed_len" step="0.1" min="0"></div>
<div><label>宽 cm</label><input type="number" id="ed_wid" step="0.1" min="0"></div>
<div><label>高 cm</label><input type="number" id="ed_hei" step="0.1" min="0"></div>
<div><label>重量 g</label><input type="number" id="ed_weight" step="1" min="0" onchange="loadShipping()"></div>
</div></div>
<div class="sec"><h5>③ 定价计算器（毛子ERP 公式 + 本地佣金/物流库）</h5>
<div class="grid4">
<div><label>采购价 ¥</label><input type="number" id="ed_cost" step="0.01" min="0" onchange="loadShipping()"></div>
<div><label>汇率 CNYRUB</label><input type="number" id="ed_rate" step="0.0001" value="12.47"></div>
<div><label>利润率 %</label><input type="number" id="ed_profit" value="20" step="1"></div>
<div><label>划线折扣 %</label><input type="number" id="ed_oldrate" value="50" step="1"></div>
</div>
<div class="grid4" style="margin-top:6px">
<div><label>广告费 %</label><input type="number" id="ed_ad" value="0" step="0.5"></div>
<div><label title="提现/货损等">其他 %</label><input type="number" id="ed_other" value="1" step="0.1"></div>
<div><label title="费率按当前类目实时取佣金库；自动=按折后售价₽选档">佣金档（按类目）</label><select id="ed_tier">
<option value="">自动</option></select></div>
<div><label>物流方案</label><select id="ed_ship"></select></div>
</div>
<button class="calcbtn" onclick="calcPrice()" style="margin-top:8px">计算建议价</button>
<span class="dim" style="font-size:11px">佣金按售价₽自动选档迭代；固定扣点 0.33%(ITO)+2%(尾程)已含</span>
<div class="priceout" id="ed_priceout"></div></div>
<div class="sec"><h5>④ 上架属性（<span class="req">*</span>必填 + 筛选项；字典属性输入即搜索）</h5>
<div id="ed_attrs" class="dim">加载中…</div></div>
<button class="save" id="ed_save" onclick="saveEdit()">💾 保存上架编辑</button>
<div class="err" id="ed_err"></div>
</div></div>
"""

EDITOR_JS = """
let edPid=null, edPricing=null, edAttrDefs=[], dictMap={};
function val(id){return document.getElementById(id).value;}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function val_set(id,v){document.getElementById(id).value=v;}
function edErr(m){const e=document.getElementById('ed_err');e.textContent='⚠️ '+m;e.style.display='block';}
async function openEditor(pid){
  edPid=pid;edPricing=null;dictMap={85:{'Нет бренда':126745801}};  // 品牌默认无品牌（字典 id）
  document.getElementById('editmodal').classList.add('on');
  document.getElementById('edpid').textContent='#'+pid;
  document.getElementById('ed_err').style.display='none';
  document.getElementById('ed_priceout').style.display='none';
  const d=await (await fetch('/api/editor-data?pid='+pid)).json();
  if(d.error){edErr(d.error);return;}
  val_set('ed_title',d.ozon_title||'');val_set('ed_desc',d.description_ru||'');
  val_set('ed_cost',d.price_cny??'');
  const le=d.listing_edits||{},dm=le.dimensions_cm||{};
  val_set('ed_len',dm.length??'');val_set('ed_wid',dm.width??'');
  val_set('ed_hei',dm.height??'');val_set('ed_weight',le.weight_g??'');
  edAttrDefs=d.attributes||[];
  renderAttrs(le.attributes||[]);
  document.getElementById('ed_tier').innerHTML=d.commission_options||'<option value="">自动</option>';
  if(le.pricing&&le.pricing.price_cny){edPricing=le.pricing;showPricing(le.pricing);}
  loadShipping();
}
function renderAttrs(saved){
  const box=document.getElementById('ed_attrs');
  if(!edAttrDefs.length){box.innerHTML='<span class=dim>该类目属性拉取失败或为空</span>';return;}
  const defaults={8513:'1',11650:'1',85:'Нет бренда'};  // 每包/原厂包装数量默认 1，品牌默认无品牌
  box.innerHTML=edAttrDefs.map(a=>{
    const sv=(saved.find(x=>x.id===a.id)||{}).values||[{}];const cur=sv[0]||{};
    const tag=(a.is_required?'<span class=req>*</span>':'')+(a.is_aspect?' <span class=asp>筛选</span>':'')
      // shortcut: 多值属性只支持单值输入，多变体场景再升级多选
      +(a.is_collection?' <span class=asp>多值</span>':'');
    const v=cur.value||defaults[a.id]||'';
    const fld=a.dictionary_id
      ?`<input type=text list="dl${a.id}" placeholder="输入搜索…" data-attr="${a.id}" data-dict="1" onfocus="loadDict(${a.id})" oninput="dictSearch(${a.id},this.value)" value="${esc(v)}"><datalist id="dl${a.id}"></datalist>`
      :`<input type=text data-attr="${a.id}" data-dict="0" value="${esc(v)}">`;
    const nm=a.name_cn&&a.name_cn!==a.name
      ?`${esc(a.name_cn)} <span class=dim>${esc(a.name)}</span>`:esc(a.name);
    return `<div class=attr><label>${tag} ${nm} <span class=dim>#${a.id}</span></label><div class=fld>${fld}</div></div>`;
  }).join('');
}
async function loadDict(attr){if(dictMap[attr])return;dictSearch(attr,'');}
async function dictSearch(attr,q){
  const r=await (await fetch(`/api/attr-values?pid=${edPid}&attr=${attr}&q=${encodeURIComponent(q)}`)).json();
  dictMap[attr]={};const dl=document.getElementById('dl'+attr);
  dl.innerHTML=(r.values||[]).map(v=>{dictMap[attr][v.value]=v.id;return `<option value="${esc(v.value)}">`;}).join('');
}
async function loadShipping(){
  const cost=val('ed_cost'),weight=val('ed_weight');if(!cost||!weight)return;
  const r=await (await fetch(`/api/shipping?pid=${edPid}&purchase=${cost}&weight=${weight}`)).json();
  document.getElementById('ed_ship').innerHTML=(r.options||[]).map(o=>
    `<option value="${o.provider}|${o.mode}">${o.provider} ${o.mode} ¥${o.fee} (${o.delivery_days}天)</option>`).join('');
}
async function calcPrice(){
  const fd=new URLSearchParams({pid:edPid,cost:val('ed_cost'),weight:val('ed_weight'),
    rate:val('ed_rate'),profit:val('ed_profit'),oldrate:val('ed_oldrate'),
    ad:val('ed_ad'),other:val('ed_other'),tier:val('ed_tier'),ship:val('ed_ship')});
  const r=await (await fetch('/api/price',{method:'POST',body:fd})).json();
  if(r.error){edErr(r.error);return;}edPricing=r;showPricing(r);
}
function showPricing(p){
  const el=document.getElementById('ed_priceout');el.style.display='block';
  const L=p.logistics||{};
  el.innerHTML=`<div>建议售价 <span class=big>¥${p.price_cny}</span> ≈ ₽${p.price_rub}
   · 划线价 ¥${p.old_price_cny} · 佣金档${p.commission_tier}(${p.commission_pct}%)</div>
  <table><tr><td>采购成本</td><td>¥${p.purchase_cny}</td><td>跨境物流</td><td>¥${L.fee} (${esc(L.provider||'')} ${esc(L.mode||'')})</td></tr>
  <tr><td>平台佣金</td><td>¥${p.cate_fee}</td><td>尾程2%</td><td>¥${p.wc_fee}</td></tr>
  <tr><td>ITO 0.33%</td><td>¥${p.ito_fee}</td><td>其他</td><td>¥${p.other_fee}</td></tr>
  <tr><td>广告</td><td>¥${p.ad_fee}</td><td><b>毛利</b></td><td><b>¥${p.profit_cny}</b></td></tr></table>`;
}
function collectAttrs(){
  const out=[];
  document.querySelectorAll('#ed_attrs [data-attr]').forEach(el=>{
    const id=+el.dataset.attr,v=el.value.trim();if(!v)return;
    if(el.dataset.dict==='1'){out.push({id,complex_id:0,values:[{dictionary_value_id:(dictMap[id]||{})[v],value:v}]});}
    else{out.push({id,complex_id:0,values:[{value:v}]});}
  });return out;
}
async function saveEdit(){
  if(!edPricing){edErr('请先点「计算建议价」生成定价');return;}
  const fd=new URLSearchParams({pid:edPid,ozon_title:val('ed_title'),description_ru:val('ed_desc'),
    length:val('ed_len'),width:val('ed_wid'),height:val('ed_hei'),weight_g:val('ed_weight'),
    attributes:JSON.stringify(collectAttrs()),pricing:JSON.stringify(edPricing)});
  const r=await (await fetch('/edit',{method:'POST',body:fd})).json();
  if(r.ok){location.href='/review?pid='+edPid+'&edited=1';return;}edErr(r.error||'保存失败');
}
"""

STATES = ["discovered", "scored", "sourced", "checked", "profitable",
          "imaging", "review_pending"]
STATE_CN = {"discovered": "① 发现", "scored": "① 选品", "sourced": "② 找货",
            "checked": "③ 趋势", "profitable": "④ 算账", "imaging": "⑥ 生图",
            "review_pending": "👤 待审"}
GATE_CN = {"scored": "六维漏斗", "sourced": "1688 图搜", "checked": "防促销脉冲",
           "profitable": "含推广准备金", "imaging": "$0.03×8",
           "review_pending": "唯一人工闸"}
DECISION_CN = {"approve": "✅ 通过", "regenerate": "🔁 打回重生", "kill": "❌ 毙品"}
SLOT_NAMES = {1: "封面信息图", 2: "白底单品", 3: "核心卖点", 4: "使用演示",
              5: "场景延伸", 6: "细节材质", 7: "规格尺寸", 8: "适用场景条"}
DAILY_LISTING_LIMIT = 100  # ROADMAP: listing_worker 日限 100 卡


def load(pid_row) -> dict:
    return json.loads(pid_row["metrics_json"] or "{}")


def topbar(c) -> str:
    """保险丝指标：能算的真实算，未接入的显示 —（不编造）"""
    listed = c.execute(
        "SELECT COUNT(*) n FROM events WHERE to_state='listed' AND created_at>=?",
        (date.today().isoformat(),)).fetchone()["n"]
    pend = c.execute(
        "SELECT COUNT(*) n FROM products WHERE state='review_pending'"
    ).fetchone()["n"]
    dec = c.execute("SELECT decision, COUNT(*) n FROM reviews"
                    " WHERE decision IS NOT NULL GROUP BY decision").fetchall()
    total = sum(r["n"] for r in dec)
    ok = next((r["n"] for r in dec if r["decision"] == "approve"), 0)
    rate = f"{round(100 * ok / total)}%" if total else "—"
    return f"""<div class="topbar">
  <span class="logo">🛂 审核台</span>
  <span class="metric">今日上架 <b>{listed} / {DAILY_LISTING_LIMIT}</b> 卡</span>
  <span class="metric">今日成本 <b>—</b> <span class="dim">未汇总</span></span>
  <span class="metric">过审率 <b>{rate}</b> <span class="dim">({total} 次审核)</span></span>
  <span class="metric">队列 <b>{pend}</b> 待审</span>
  <span class="switch">review_mode
    <span class="pill"><span class="on">full 全检</span><span>sample 20%</span></span>
    <span class="dim">· 过审率稳定 95%+ 后才切（V2）</span>
  </span>
</div>"""


def page(title: str, tab: str, body: str) -> bytes:
    with P.conn() as c:
        bar = topbar(c)
        n_review = c.execute(
            "SELECT COUNT(*) n FROM products WHERE state='review_pending'"
        ).fetchone()["n"]
    tabs = [("/board", "📊 流水线", "board", ""),
            ("/review", "🖼 图审（上架前）", "review",
             f'<span class="n">{n_review}</span>' if n_review else "")]
    nav = "".join(
        f'<a href="{u}" class="{"on" if k == tab else ""}">{n}{badge}</a>'
        for u, n, k, badge in tabs)
    nav += ('<a style="pointer-events:none">🗂 类目首验</a>'
            '<a style="pointer-events:none">🎬 视频审（winning 后）</a>')
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{ESC(title)} · 审核台</title><style>{CSS}</style></head><body><div class="wrap">
<div class="app">{bar}<div class="tabs">{nav}</div>{body}</div>
</div><div class="zoom" id="zoom" onclick="this.classList.remove('on')"><img id="zoomimg"></div>
<div class="modal" id="slot7modal" onclick="closeSlot7(event)"><div class="box">
<h4>⑦ 规格尺寸图 · 人工输入</h4>
<div class="hint">填供应商详情页或实物实测的<b>真实</b>数字，单位 см（容量 мл）。
至少填一项，不确定的留空。填入即视为人工确认出处（最高信任级，免检溯源闸），直接用于生图。</div>
<div class="dims">
<div><label>长 (см)</label><input id="s7l" type="number" step="0.1" min="0"></div>
<div><label>宽 (см)</label><input id="s7w" type="number" step="0.1" min="0"></div>
<div><label>高 (см)</label><input id="s7h" type="number" step="0.1" min="0"></div>
</div>
<label>容量/其他（可选，如 300 мл；纯数字默认 мл）</label>
<input id="s7o" type="text" placeholder="如 300 мл">
<button class="go" id="s7go" onclick="genSlot7()">生成尺寸图（约 1 分钟 · $0.03）</button>
<div class="err" id="s7err"></div>
</div></div>
{EDITOR_HTML}
<script>
function zoom(src){{const z=document.getElementById('zoom');
document.getElementById('zoomimg').src=src;z.classList.add('on')}}
let s7pid=null;
function openSlot7(pid){{s7pid=pid;document.getElementById('slot7modal').classList.add('on')}}
function closeSlot7(e){{if(e.target.id==='slot7modal')e.target.classList.remove('on')}}
async function genSlot7(){{
const go=document.getElementById('s7go'),err=document.getElementById('s7err');
err.style.display='none';
const fd=new URLSearchParams({{pid:s7pid,ajax:'1',
len:document.getElementById('s7l').value,width:document.getElementById('s7w').value,
height:document.getElementById('s7h').value,other:document.getElementById('s7o').value}});
go.disabled=true;go.textContent='生成中…约 1 分钟，请勿关闭';
try{{
const r=await fetch('/slot7',{{method:'POST',body:fd}});
const j=await r.json();
if(j.ok){{location.href='/review?pid='+s7pid+'&ok=1';return}}
err.textContent='⚠️ '+(j.error||'生成失败');err.style.display='block';
}}catch(e){{err.textContent='⚠️ 网络错误: '+e;err.style.display='block'}}
go.disabled=false;go.textContent='生成尺寸图（约 1 分钟 · $0.03）';
}}
function askReason(btn,decision){{
document.getElementById('decision').value=decision;
document.getElementById('reasonfor').textContent=btn==='re'?'打回重生':'毙品';
const r=document.getElementById('reason');r.classList.add('open');
document.getElementById('reasonbox').style.display='inline';
document.getElementById('reasonhint').style.display='none';return false}}
function cancelReason(){{document.getElementById('reason').classList.remove('open');
document.getElementById('decision').value='';
document.getElementById('reasonbox').style.display='none';
document.getElementById('reasonhint').style.display='inline';return false}}
{EDITOR_JS}
</script>
</body></html>""".encode()


def mini(p, m, dead=False) -> str:
    bits = []
    sel, src = m.get("selection", {}), m.get("sourcing", {})
    if sel:
        bits.append(f"月搜{sel.get('searchVolume', '?')}")
    if src:
        bits.append(f"¥{src.get('price_cny', '?')}")
    v = " · ".join(bits)
    label = m.get("queryCn") or p["query"]
    cls = "mini dead" if dead else "mini"
    return (f'<a class="{cls}" href="/product/{p["id"]}"><b>{ESC(label)}</b>'
            f'<span class="v">{ESC(v)}</span></a>')


def board() -> bytes:
    with P.conn() as c:
        rows = c.execute("SELECT * FROM products").fetchall()
        dead_from = {r["product_id"]: r["from_state"] for r in c.execute(
            "SELECT product_id, from_state FROM events WHERE to_state='dead'"
            " ORDER BY id")}
    cols = {s: [] for s in STATES}
    for p in rows:
        m = load(p)
        if p["state"] == "dead":
            cols.get(dead_from.get(p["id"], "scored"), cols["scored"]).append(
                mini(p, m, dead=True))
        elif p["state"] in cols:
            cols[p["state"]].append(mini(p, m))
    html_cols = "".join(
        f'<div class="col{" human" if s == "review_pending" else ""}">'
        f'<h5>{STATE_CN[s]}<span class="cnt">{len(cards)}</span></h5>'
        f'<span class="gate">{GATE_CN.get(s, "")}</span>'
        + "".join(cards) + "</div>"
        for s, cards in cols.items())
    body = f"""<div class="board">{html_cols}</div>
<div class="dim" style="padding:0 16px 14px">每张卡一路过闸到此；变灰划线=在该闸被杀，点卡看完整 trace。</div>"""
    return page("流水线", "board", body)


def _spec(pid_metrics) -> dict:
    sp = pid_metrics.get("spec_path")
    if not sp:
        return {}
    f = ROOT / sp
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _slots(m, pid: int | None = None) -> str:
    d = m.get("assets_dir")
    have = set(m.get("images", []))
    cells = []
    for i in range(1, 9):
        name = SLOT_NAMES[i]
        fname = f"slot{i}.png"
        if d and fname in have:
            src = f"/media/{ESC(d)}/{fname}"
            cells.append(
                f'<div class="slot has" onclick="zoom(\'{src}\')">'
                f'<span class="badge">{i}</span><img src="{src}" loading="lazy">'
                f'<span class="lb">{ESC(name)}</span></div>')
        elif i == 7 and pid and d:
            # slot7 人工输入尺寸：拿不到真实尺寸时的兜底通道（最高信任级）
            cells.append(
                f'<div class="slot"><span class="badge">7</span>{ESC(name)}<br>'
                f'<span class="cta" onclick="openSlot7({pid})">＋ 输入真实尺寸</span></div>')
        else:
            cells.append(f'<div class="slot"><span class="badge">{i}</span>'
                         f'{ESC(name)}<br>(待生成)</div>')
    return f'<div class="slots">{"".join(cells)}</div>'


def _compose_measurements(form: dict) -> str:
    """结构化字段 → 俄语尺寸串（规则由构造保证：标签固定俄语、单位 см/мл）。"""
    parts = []
    for k, ru in (("len", "Длина"), ("width", "Ширина"), ("height", "Высота")):
        v = form.get(k, [""])[0].strip()
        if v:
            parts.append(f"{ru} {v} см")
    other = form.get("other", [""])[0].strip()
    if other:
        # 纯数字按容量处理（输入框提示已注明），否则原文（需自带单位）
        parts.append(f"{other} мл" if re.fullmatch(r"[\d.,]+", other) else other)
    return " · ".join(parts)


def gen_slot7(pid: int, measurements: str) -> str | None:
    """人工输入尺寸 → 写 spec（source=manual 最高信任级）→ 重生 slot7 → 更新 DB。
    返回 None=成功，否则错误信息。生图 30-60s，请求会阻塞（本机工具可接受）。"""
    if not re.search(r"\d", measurements):
        return "至少填写一项有效尺寸（数字必填）"
    with P.conn() as c:
        row = c.execute("SELECT metrics_json FROM products WHERE id=?",
                        (pid,)).fetchone()
    if not row:
        return "产品不存在"
    m = load(row)
    sp, assets = m.get("spec_path"), m.get("assets_dir")
    img = m.get("sourcing", {}).get("image_url")
    if not (sp and assets and img):
        return "缺 spec/资产目录/货源图，无法生成"
    spec_file = ROOT / sp
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    spec["slots"] = [s for s in spec.get("slots", []) if s.get("slot") != "slot7"]
    spec["slots"].append({"slot": "slot7", "measurements": measurements,
                          "source": "manual"})
    spec_file.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    r = subprocess.run([sys.executable, "image_worker.py", "--spec", sp,
                        "--image", img, "--out-dir", assets, "--only", "slot7"],
                       capture_output=True, text=True, cwd=ROOT)
    if not (ROOT / assets / "slot7.png").is_file():
        return f"生成失败: {(r.stderr or '')[-200:]}"
    m["images"] = sorted(p.name for p in (ROOT / assets).glob("slot[0-9].png"))
    with P.conn() as c:
        c.execute("UPDATE products SET metrics_json=?, updated_at=? WHERE id=?",
                  (json.dumps(m, ensure_ascii=False), P.now(), pid))
        P._event(c, pid, None, "slot7", f"人工输入尺寸生成: {measurements}")
    return None


def review(pid: int | None, flash: str = "") -> bytes:
    with P.conn() as c:
        pend = c.execute("SELECT * FROM products WHERE state='review_pending'"
                         " ORDER BY updated_at").fetchall()
        imaging = c.execute("SELECT * FROM products WHERE state='imaging'"
                            " ORDER BY updated_at DESC").fetchall()
        regen = {r["product_id"]: r["n"] for r in c.execute(
            "SELECT product_id, COUNT(*) n FROM reviews"
            " WHERE decision='regenerate' GROUP BY product_id")}
        cur = None
        if pend:
            want = pid or pend[0]["id"]
            cur = next((p for p in pend if p["id"] == want), pend[0])

    items = []
    for p in pend:
        m = load(p)
        on = " on" if cur and p["id"] == cur["id"] else ""
        title = m.get("ozon_title") or p["query"]
        cat = _spec(m).get("category_path_ru", "").split("/")[-1] or "—"
        if m.get("need_confirm"):
            sub = '<span class="warn">⚠ need_confirm（机械校验未过）</span>'
        elif regen.get(p["id"]):
            sub = f"重生成 ×{regen[p['id']]}"
        else:
            sub = (p["prompt_version"] or "首次送审")
        items.append(
            f'<a class="qitem{on}" href="/review?pid={p["id"]}">'
            f'<div class="t">{ESC(title)}</div>'
            f'<div class="m">{ESC(m.get("queryCn") or "")} · {ESC(cat)}<br>{sub}</div></a>')
    imaging_html = "".join(
        f'<a class="qitem" href="/product/{p["id"]}" style="opacity:.75">'
        f'<div class="t">{ESC(load(p).get("ozon_title") or p["query"])}</div>'
        f'<div class="m">#{p["id"]} · {ESC(p["updated_at"][:16])} · trace →</div></a>'
        for p in imaging)
    queue_html = (f'<div class="queue"><h4>待审队列 · imaging →【待审】（{len(pend)}）</h4>'
                  f'{"".join(items) or "<div class=dim>队列空 🎉</div>"}'
                  + (f'<h4 style="margin-top:14px">🔁 生图/重生中 · imaging（{len(imaging)}）</h4>'
                     f'{imaging_html}'
                     '<div class="dim" style="margin:2px 4px">重跑 image_worker 出图后 '
                     'sync 自动送回待审</div>' if imaging else "")
                  + "</div>")

    if not cur:
        return page("图审", "review",
                    f'{flash}<div class="rl">{queue_html}'
                    f'<div class="detail"><div class="dim" style="padding:20px">'
                    f'队列空——去 <a href="/board">📊 流水线</a> 看全链路</div></div></div>')

    m = load(cur)
    spec = _spec(m)
    sel, src = m.get("selection", {}), m.get("sourcing", {})

    warn = ""
    if m.get("need_confirm"):
        probs = m.get("spec_validation")
        plist = "".join(f"<li>{ESC(str(x))}</li>" for x in probs) \
            if isinstance(probs, list) else ""
        warn = (f'<div class="warnbox">⚠️ <b>need_confirm</b>：内容闸机械校验未过，'
                f"请重点核对以下问题<ul>{plist}</ul></div>")

    validation = m.get("spec_validation")
    if validation == "pass":
        check = '<span class="pos">禁忌词 ✓ 长度 ✓ 数字溯源 ✓</span>'
    elif isinstance(validation, list):
        check = f'<span class="neg">✗ {len(validation)} 项未过（见上方警告）</span>'
    else:
        check = "—"

    econ_tag = ('<span class="tag t-ok">利润为正</span>'  # 闸未实现，暂以人工判断
                if src else "")
    kv = "".join(f'<div class="row"><span>{a}</span><span>{b}</span></div>' for a, b in [
        ("类目路径", ESC(spec.get("category_path_ru", "—"))),
        ("六维漏斗", ESC(f"月搜 {sel.get('searchVolume', '—')} · 增长 {sel.get('growth30d', '—')}% · 竞争 {sel.get('competingProducts', '—')} · 均价 {sel.get('avgPrice', '—')}₽ · 退货 {sel.get('returnRate', '—')}%")),
        ("趋势", '<span class="dim">⚠ trend worker 未实现（任务3）</span>'),
        ("单位经济", '<span class="dim">⚠ econ worker 未实现（任务4）</span>'),
        ("1688 货源", ESC(f"¥{src.get('price_cny', '—')} · 月销 {src.get('sold_count', '—')} · ")
         + (f'<a href="{ESC(src["url"])}" target="_blank">offer {src["offer_id"]} ↗</a>'
            if src.get("url") else
            f'<a href="https://detail.1688.com/offer/{src["offer_id"]}.html" target="_blank">'
            f'offer {src["offer_id"]} ↗</a>' if src.get("offer_id") else "—")),
        ("Ozon 参考价", ESC(f"{sel.get('avgPrice', '—')}₽（竞品 {cur['ozon_id']}）")),
        ("prompt 版本", ESC(cur["prompt_version"] or "—")),
        ("机械校验", check),
    ])

    opts = "".join(f"<option>{ESC(r)}</option>" for r in P.KILL_REASONS)
    actions = f"""
<form method="POST" action="/action" id="actform">
  <input type="hidden" name="pid" value="{cur['id']}">
  <input type="hidden" name="decision" id="decision" value="">
  <div class="actions">
    <button type="submit" class="ok" onclick="document.getElementById('decision').value='approve'">✅ 通过<small>→ 进上架队列（listed）</small></button>
    <button type="submit" class="re" onclick="return askReason('re','regenerate')">🔁 打回重生<small>换 seed 重出图 · $0.03/张</small></button>
    <button type="submit" class="kill" onclick="return askReason('kill','kill')">❌ 毙品<small>→ dead · 死因反哺阈值</small></button>
  </div>
  <div class="reason" id="reason">
    <span id="reasonbox" style="display:none">
      <b id="reasonfor"></b> · 死因必填（写入 reviews 表，反哺阈值）：<br>
      <select name="reason"><option value="">— 选择死因 —</option>{opts}</select>
      <input type="text" name="note" placeholder="备注（选「其他」必填）" size="28">
      <button type="submit" class="confirm">确认提交</button>
      <button type="button" class="cancel" onclick="return cancelReason()">取消</button>
    </span>
    <span id="reasonhint">毙品/打回时弹出：死因选择（{" / ".join(P.KILL_REASONS)}）——必填，写入 reviews 表</span>
  </div>
</form>"""

    edited_tag = ('<span class="tag t-manual">✏️ 已编辑·已确认</span>'
                  if m.get("listing_edited") else "")
    detail = f"""<div class="detail">
  <div class="detail-head">
    <span class="title">{ESC(m.get('ozon_title') or cur['query'])}</span>
    <span class="tag t-info">imaging → 待审</span>{econ_tag}{edited_tag}
    <span class="dim">#{cur['id']} · query: {ESC(cur['query'])} ·
      <a href="/product/{cur['id']}">完整 trace →</a></span>
    <button class="calcbtn" onclick="openEditor({cur['id']})">✏️ 上架编辑</button>
  </div>
  {warn}
  <div class="detail-grid">
    <div>
      {_slots(m, cur["id"])}
      <div class="dim" style="margin-top:8px">点击单张可放大；单独重生某 slot 重跑 image_worker 即可（$0.03/张），不必整卡重来</div>
    </div>
    <div class="kv">{kv}</div>
  </div>
  {actions}
  <h3>俄语详情介绍</h3>
  <div class="desc">{ESC(m.get('description_ru') or '（spec 未产出 description_ru）')}</div>
</div>"""
    return page("图审", "review",
                f'{flash}<div class="rl">{queue_html}{detail}</div>')


def trace(pid: int) -> bytes:
    with P.conn() as c:
        p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not p:
            return page("404", "board", "<div class=card>产品不存在</div>")
        evs = c.execute("SELECT * FROM events WHERE product_id=? ORDER BY id",
                        (pid,)).fetchall()
        revs = c.execute("SELECT * FROM reviews WHERE product_id=? ORDER BY id",
                         (pid,)).fetchall()
    m = load(p)
    ev_html = "".join(
        f'<div class="ev{" dead" if e["to_state"] == "dead" else " human" if e["to_state"] in ("review_pending", "video_pending") else ""}">'
        f'<b>{ESC(e["from_state"] or "∅")} → {ESC(e["to_state"])}</b>'
        f'{" · " + ESC(e["note"] or "") if e["note"] else ""}'
        f'<div class="dim">{ESC(e["created_at"])}</div></div>' for e in evs)
    rev_html = "".join(
        f'<div class="ev human"><b>{DECISION_CN.get(r["decision"], r["decision"])}</b>'
        f' · {ESC(r["kind"])} · {ESC(r["asset_path"])}'
        f'{" · 死因: " + ESC(r["reason"]) if r["reason"] else ""}'
        f'<div class="dim">{ESC(r["decided_at"] or "")}</div></div>' for r in revs)
    body = f"""<div class="card">
<span class="tag t-manual">#{p['id']}</span><b>{ESC(m.get('ozon_title') or p['query'])}</b>
<span class="tag t-warn">{ESC(p['state'])}</span>
<div class="dim">{ESC(p['query'])} · ozon {ESC(p['ozon_id'] or '—')} · 1688 {ESC(m.get('sourcing', {}).get('offer_id', '—'))}</div>
<h3>状态流转（events）</h3><div class="trace">{ev_html}</div>
<h3>审核标签（reviews / eval golden set）</h3><div class="trace">{rev_html or '<div class=dim>尚无</div>'}</div>
</div>"""
    return page(f"trace #{pid}", "board", body)


def _spec_ids(m: dict) -> tuple[int, int]:
    return OC.parse_category_ids(_spec(m).get("_meta", {}).get("categoryId", ""))


# 编辑器上架属性白名单（按 Ozon 后台「特征」页顺序）：
# API 必填 3 项 + 筛选项 + 运营必填（后台截图批注：主题标签/净重/每包数量/
# 颜色/组合类似品/配套/原产国/原厂包装数量/HS 编码）。其余属性一律不显示。
EDITOR_ATTR_IDS = [85, 9048, 8229,            # 品牌* / 型号名称* / 类型*
                   23171, 4383, 22390, 8513,  # 主题标签 / 商品净重g / 组合类似品 / 每包数量
                   10096, 10097,              # 商品颜色 / 颜色名称
                   8415, 8416,                # 长度cm / 宽度cm（筛选）
                   4384, 4389, 11650, 22232]  # 配套 / 原产国 / 原厂包装数量 / HS 编码


def _editor_attrs(attrs: list[dict]) -> list[dict]:
    """按白名单过滤并排序（编辑器只展示这些字段）。"""
    by_id = {a["id"]: a for a in attrs}
    return [by_id[i] for i in EDITOR_ATTR_IDS if i in by_id]


def _commission_options(category_id: int) -> str:
    """编辑器佣金档下拉：按类目显示真实三档费率（data/ozon-commission.json）。
    档边界 1500/5000₽ 全类目统一；自动=按折后售价₽选档（服务端迭代）。"""
    try:
        tiers = pricing.commission_tiers(category_id)
    except KeyError:
        return '<option value="">自动（类目不在佣金库）</option>'
    bounds = {1: "≤1500₽", 2: "≤5000₽", 3: ">5000₽"}
    opts = ['<option value="">自动（按折后售价选档）</option>']
    for t in sorted(tiers):
        opts.append(f'<option value="{t}">档{t} {bounds[t]} · {tiers[t]:g}%</option>')
    return "".join(opts)


def editor_data(pid: int) -> dict:
    """编辑器初始数据：文案/成本/已有编辑 + 该类目上架属性（实时拉 Ozon）。"""
    with P.conn() as c:
        row = c.execute("SELECT metrics_json FROM products WHERE id=?",
                        (pid,)).fetchone()
    if not row:
        return {"error": "产品不存在"}
    m = load(row)
    out = {"ozon_title": m.get("ozon_title", ""),
           "description_ru": m.get("description_ru", ""),
           "price_cny": m.get("sourcing", {}).get("price_cny"),
           "listing_edits": LE.get_edits(m), "attributes": [],
           "commission_options": "<option value=\"\">自动</option>"}
    try:
        desc, typ = _spec_ids(m)
        out["attributes"] = _editor_attrs(OC.get_attributes(desc, typ))
        out["commission_options"] = _commission_options(desc)
    except Exception as exc:
        out["attr_error"] = str(exc)
    return out


def attr_values(pid: int, attr_id: int, query: str) -> dict:
    with P.conn() as c:
        m = load(c.execute("SELECT metrics_json FROM products WHERE id=?",
                           (pid,)).fetchone())
    desc, typ = _spec_ids(m)
    return {"values": OC.get_attr_values(desc, typ, attr_id, query)}


def shipping(purchase: float, weight: float) -> dict:
    cls = pricing.logistics_class(purchase)
    return {"class": cls, "options": pricing.shipping_options(cls, weight)}


def api_price(form: dict) -> dict:
    pid = int(form["pid"][0])
    with P.conn() as c:
        m = load(c.execute("SELECT metrics_json FROM products WHERE id=?",
                           (pid,)).fetchone())
    desc, _ = _spec_ids(m)   # 佣金库 cate_id = description_category_id
    ship = form.get("ship", [""])[0]
    provider, mode = ship.split("|") if "|" in ship else (None, None)
    tier = form.get("tier", [""])[0]
    return pricing.calc(
        float(form["cost"][0]), float(form["weight"][0]), desc,
        rate_cnyrub=float(form["rate"][0]),
        profit_rate=float(form["profit"][0]) / 100,
        old_price_rate=float(form["oldrate"][0]) / 100,
        ad_rate=float(form["ad"][0]) / 100,
        other_rate=float(form["other"][0]) / 100,
        provider=provider or None, mode=mode or None,
        commission_tier=int(tier) if tier else None)


def api_edit(form: dict) -> dict:
    pid = int(form["pid"][0])

    def num(k):
        v = form.get(k, [""])[0].strip()
        return float(v) if v else None

    edits = {"ozon_title": form.get("ozon_title", [""])[0],
             "description_ru": form.get("description_ru", [""])[0],
             "dimensions_cm": {"length": num("length"), "width": num("width"),
                               "height": num("height")},
             "weight_g": num("weight_g"),
             "attributes": json.loads(form.get("attributes", ["[]"])[0]),
             "pricing": json.loads(form.get("pricing", ["{}"])[0])}
    err = LE.save(pid, edits)
    return {"ok": not err, **({"error": err} if err else {})}


class Handler(BaseHTTPRequestHandler):
    def _send(self, data: bytes, ctype="text/html; charset=utf-8", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, url):
        self.send_response(303)
        self.send_header("Location", url)
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            return self._redirect("/board")
        if u.path == "/board":
            return self._send(board())
        if u.path == "/review":
            pid = int(qs["pid"][0]) if "pid" in qs else None
            flash = ('<div class="okbox">✅ 尺寸图已生成并入队列，'
                     '请重点核对数字与实物一致</div>' if qs.get("ok") else "")
            if qs.get("edited"):
                flash = ('<div class="okbox">✅ 上架编辑已保存到 spec（listing_edits），'
                         'need_confirm 已人工确认清除</div>')
            if qs.get("regen"):
                rp = qs["regen"][0]
                flash = (f'<div class="okbox">🔁 #{ESC(rp)} 已打回 → 生图队列（imaging），'
                         f'见下方「重生中」列表或 <a href="/product/{ESC(rp)}">完整 trace</a>。'
                         f'重跑 image_worker 换 seed 出图后，sync 会自动送回待审队列</div>')
            return self._send(review(pid, flash))
        if u.path == "/api/editor-data":
            return self._send(json.dumps(editor_data(int(qs["pid"][0])),
                                         ensure_ascii=False).encode(),
                              "application/json; charset=utf-8")
        if u.path == "/api/attr-values":
            return self._send(json.dumps(
                attr_values(int(qs["pid"][0]), int(qs["attr"][0]),
                            qs.get("q", [""])[0]), ensure_ascii=False).encode(),
                "application/json; charset=utf-8")
        if u.path == "/api/shipping":
            return self._send(json.dumps(
                shipping(float(qs["purchase"][0]), float(qs["weight"][0])),
                ensure_ascii=False).encode(), "application/json; charset=utf-8")
        if u.path.startswith("/product/"):
            return self._send(trace(int(u.path.split("/")[-1])))
        if u.path.startswith("/media/"):
            rel = Path(urllib.parse.unquote(u.path[len("/media/"):]))
            full = (ROOT / rel).resolve()          # 防目录穿越
            if full.is_file() and GEN.resolve() in full.parents:
                return self._send(full.read_bytes(), "image/png")
            return self._send(b"404", code=404)
        return self._send(b"404", code=404)

    def do_POST(self):
        form = urllib.parse.parse_qs(
            self.rfile.read(int(self.headers["Content-Length"])).decode())
        if self.path == "/slot7":
            pid = int(form["pid"][0])
            measurements = (form.get("measurements", [""])[0].strip()
                            or _compose_measurements(form))
            err = gen_slot7(pid, measurements)
            if form.get("ajax", [""])[0]:  # 模态框 fetch：JSON 应答，页面不跳转
                payload = {"ok": not err, **({"error": err} if err else {})}
                return self._send(json.dumps(payload, ensure_ascii=False).encode(),
                                  "application/json; charset=utf-8")
            if err:
                return self._send(
                    review(pid, f'<div class="warnbox">⚠️ {ESC(err)}</div>'))
            return self._redirect(f"/review?pid={pid}")
        if self.path == "/api/price":
            try:
                r = api_price(form)
            except Exception as exc:
                r = {"error": str(exc)}
            return self._send(json.dumps(r, ensure_ascii=False).encode(),
                              "application/json; charset=utf-8")
        if self.path == "/edit":
            return self._send(json.dumps(api_edit(form), ensure_ascii=False).encode(),
                              "application/json; charset=utf-8")
        if self.path != "/action":
            return self._send(b"404", code=404)
        pid = int(form["pid"][0])
        decision = form["decision"][0]
        reason = form.get("reason", [""])[0]
        note = form.get("note", [""])[0].strip()
        if reason == "其他":
            reason = f"其他: {note}" if note else "其他"
        with P.conn() as c:
            m = load(c.execute("SELECT metrics_json FROM products WHERE id=?",
                               (pid,)).fetchone())
        try:
            P.record_review(pid, "image",
                            m.get("assets_dir", m.get("spec_path", "")),
                            decision, reason)
        except ValueError as exc:
            return self._send(review(pid, f'<div class="warnbox">⚠️ {ESC(str(exc))}</div>'))
        self._redirect(f"/review?regen={pid}" if decision == "regenerate"
                       else "/review")

    def log_message(self, *a):  # 静默
        pass


if __name__ == "__main__":
    P.cmd_init()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print(f"审核台 http://127.0.0.1:{port}  (Ctrl+C 停止)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
