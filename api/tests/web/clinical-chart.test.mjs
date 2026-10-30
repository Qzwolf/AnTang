import assert from 'node:assert/strict';
import {test} from 'node:test';
import {glucoseValue,chartLayout,lineChart} from '../../src/antang_api/clinical_web/chart.mjs';
test('glucose units preserve original reading',()=>{assert.equal(glucoseValue(90),5);assert.equal(glucoseValue(180,'mg'),180)});
test('curve preserves source and measurement gaps without mutating observations',()=>{
 const records=[{time:'2026-10-20T16:00:00Z',value:140,source:'manual'},{time:'2026-10-20T00:00:00Z',value:90,source:'manual'},{time:'2026-10-20T03:00:00Z',value:110,source:'manual'},{time:'2026-10-20T05:00:00Z',value:100,source:'report'}];
 const original=structuredClone(records);
 const result=chartLayout(records,'2026-10-20T00:00:00Z','2026-10-21T00:00:00Z');
 assert.deepEqual(result.segments.map(s=>s.length),[2,1,1]);assert.deepEqual(records,original);
});
test('empty observations stay empty and invalid data cannot form a curve',()=>{
 assert.equal(chartLayout([],'2026-10-20','2026-10-21').points.length,0);
 assert.throws(()=>chartLayout([{time:'invalid',value:100}],'2026-10-20','2026-10-21'));
 assert.throws(()=>chartLayout([{time:'2026-10-20',value:NaN}],'2026-10-20','2026-10-21'));
});

test('Ohio CGM preserves 5 minute samples and breaks gaps longer than 15 minutes',()=>{
 const data=[{time:'2027-05-19T00:00:00Z',value:100,source:'ohio_cgm'},
 {time:'2027-05-19T00:05:00Z',value:105,source:'ohio_cgm'},
 {time:'2027-05-19T01:00:00Z',value:110,source:'ohio_cgm'}];
 assert.deepEqual(chartLayout(data,'2027-05-19','2027-05-20',.25).segments.map(s=>s.length),[2,1]);
});

test('click exposes exact values and zoom/reset preserve samples',()=>{
 class Node {constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.events={};}setAttribute(k,v){this.attrs[k]=v;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}addEventListener(k,fn){this.events[k]=fn;}}
 const previous=globalThis.document;
 globalThis.document={createElement:tag=>new Node(tag),createElementNS:(_,tag)=>new Node(tag)};
 try{
 const container=new Node('div');
 const records=[0,1,5].map(hour=>({time:'2027-05-19T0'+hour+':00:00Z',value:5.55+hour,source:'ohio_cgm'}));
 lineChart(container,records,{start:'2027-05-19T00:00:00Z',end:'2027-05-20T00:00:00Z',unit:'mmol/L'});
 const points=()=>container.children[0].children.filter(n=>n.tag==='circle');
 points()[1].events.click();
 assert.ok(container.children[1].textContent.includes('6.55 mmol/L'));
 assert.equal(points()[1].attrs['aria-pressed'],'true');
 container.children[2].children[0].events.click();assert.equal(points().length,2);
 container.children[2].children[1].events.click();assert.equal(points().length,3);
 assert.equal(records.length,3);
 }finally{globalThis.document=previous;}
});
