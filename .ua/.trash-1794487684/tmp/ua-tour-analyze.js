#!/usr/bin/env node
const fs = require('fs');

function fail(msg) {
  console.error(msg);
  process.exit(1);
}

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) fail('Usage: node ua-tour-analyze.js <input> <output>');

let input;
try {
  input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
} catch (err) {
  fail(`Failed to read input: ${err.message}`);
}

const nodes = Array.isArray(input.nodes) ? input.nodes : [];
const edges = Array.isArray(input.edges) ? input.edges : [];
const layers = Array.isArray(input.layers) ? input.layers : [];
const nodeById = new Map(nodes.map(n => [n.id, n]));
const ids = new Set(nodeById.keys());
const graphEdges = edges.filter(e => ids.has(e.source) && ids.has(e.target));

const fanIn = new Map(nodes.map(n => [n.id, 0]));
const fanOut = new Map(nodes.map(n => [n.id, 0]));
for (const e of graphEdges) {
  fanIn.set(e.target, (fanIn.get(e.target) || 0) + 1);
  fanOut.set(e.source, (fanOut.get(e.source) || 0) + 1);
}

const rank = (map, key) => [...map.entries()]
  .map(([id, value]) => ({id, [key]: value, name: nodeById.get(id)?.name || id}))
  .sort((a, b) => b[key] - a[key] || a.id.localeCompare(b.id))
  .slice(0, 20);

const sortedFanOut = [...fanOut.values()].sort((a,b)=>a-b);
const sortedFanIn = [...fanIn.values()].sort((a,b)=>a-b);
const fanOutCut = sortedFanOut[Math.max(0, Math.floor(sortedFanOut.length * 0.9) - 1)] || 0;
const fanInCut = sortedFanIn[Math.max(0, Math.floor(sortedFanIn.length * 0.25) - 1)] || 0;
const entryNames = new Set(['index.ts','index.js','main.ts','main.js','app.ts','app.js','server.ts','server.js','mod.rs','main.go','main.py','main.rs','manage.py','app.py','wsgi.py','asgi.py','run.py','__main__.py','Application.java','Main.java','Program.cs','config.ru','index.php','App.swift','Application.kt','main.cpp','main.c']);

function depthOfPath(path) {
  if (!path) return 99;
  return path.split('/').filter(Boolean).length;
}

const entryPointCandidates = nodes.map(n => {
  let score = 0;
  if (n.type === 'file') {
    if (entryNames.has(n.name)) score += 3;
    if (depthOfPath(n.filePath) <= 2) score += 1;
    if ((fanOut.get(n.id) || 0) >= fanOutCut && (fanOut.get(n.id) || 0) > 0) score += 1;
    if ((fanIn.get(n.id) || 0) <= fanInCut) score += 1;
  }
  if (n.type === 'document') {
    if (n.filePath === 'README.md') score += 5;
    else if (depthOfPath(n.filePath) === 1 && n.name.endsWith('.md')) score += 2;
  }
  return {id:n.id, score, name:n.name, summary:n.summary || ''};
}).filter(x => x.score > 0)
  .sort((a,b)=>b.score-a.score || a.id.localeCompare(b.id))
  .slice(0,5);

const codeEntry = entryPointCandidates.map(c => nodeById.get(c.id)).find(n => n && n.type === 'file')
  || nodes.filter(n => n.type === 'file').sort((a,b)=>(fanOut.get(b.id)||0)-(fanOut.get(a.id)||0))[0];
const adj = new Map(nodes.map(n => [n.id, []]));
for (const e of graphEdges) {
  if (e.type === 'imports' || e.type === 'calls') adj.get(e.source).push(e.target);
}
for (const list of adj.values()) list.sort();
const bfsTraversal = {startNode: codeEntry?.id || null, order: [], depthMap: {}, byDepth: {}};
if (codeEntry) {
  const q = [codeEntry.id];
  bfsTraversal.depthMap[codeEntry.id] = 0;
  for (let i=0; i<q.length; i++) {
    const id = q[i];
    const depth = bfsTraversal.depthMap[id];
    bfsTraversal.order.push(id);
    (bfsTraversal.byDepth[String(depth)] ||= []).push(id);
    for (const to of adj.get(id) || []) {
      if (bfsTraversal.depthMap[to] === undefined) {
        bfsTraversal.depthMap[to] = depth + 1;
        q.push(to);
      }
    }
  }
}

const pickFields = n => ({id:n.id, name:n.name, type:n.type, summary:n.summary || ''});
const nonCodeFiles = {
  documentation: nodes.filter(n=>n.type==='document').map(pickFields),
  infrastructure: nodes.filter(n=>['service','pipeline','resource'].includes(n.type)).map(pickFields),
  data: nodes.filter(n=>['table','schema','endpoint'].includes(n.type)).map(pickFields),
  config: nodes.filter(n=>n.type==='config').map(pickFields)
};

const pairEdges = new Map();
for (const e of graphEdges.filter(e => e.type === 'imports' || e.type === 'calls')) {
  pairEdges.set(`${e.source}->${e.target}`, true);
}
const clusters = [];
const seen = new Set();
for (const a of ids) {
  for (const b of ids) {
    if (a >= b) continue;
    if (pairEdges.has(`${a}->${b}`) && pairEdges.has(`${b}->${a}`)) {
      const cluster = new Set([a,b]);
      let changed = true;
      while (changed && cluster.size < 5) {
        changed = false;
        for (const n of ids) {
          if (cluster.has(n)) continue;
          let links = 0;
          for (const c of cluster) {
            if (pairEdges.has(`${n}->${c}`) || pairEdges.has(`${c}->${n}`)) links++;
          }
          if (links >= 2) { cluster.add(n); changed = true; if (cluster.size >= 5) break; }
        }
      }
      const arr = [...cluster].sort();
      const key = arr.join('|');
      if (!seen.has(key)) {
        seen.add(key);
        let edgeCount = 0;
        for (const x of arr) for (const y of arr) if (x !== y && pairEdges.has(`${x}->${y}`)) edgeCount++;
        clusters.push({nodes: arr, edgeCount});
      }
    }
  }
}
clusters.sort((a,b)=>b.edgeCount-a.edgeCount || b.nodes.length-a.nodes.length);

const nodeSummaryIndex = Object.fromEntries(nodes.map(n => [n.id, {name:n.name, type:n.type, summary:n.summary || ''}]));

const result = {
  scriptCompleted: true,
  entryPointCandidates,
  fanInRanking: rank(fanIn, 'fanIn'),
  fanOutRanking: rank(fanOut, 'fanOut'),
  bfsTraversal,
  nonCodeFiles,
  clusters: clusters.slice(0,10),
  layers: {count: layers.length, list: layers.map(l=>({id:l.id,name:l.name,description:l.description}))},
  nodeSummaryIndex,
  totalNodes: nodes.length,
  totalEdges: graphEdges.length
};

try {
  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2));
} catch (err) {
  fail(`Failed to write output: ${err.message}`);
}
