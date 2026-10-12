"use strict";
// Unit tests execute the actual searchCards function extracted from background.js
// against realistic Mercado Livre-like DOM shapes, without external requests.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");

const file=fs.readFileSync(path.join(__dirname,"../../extensions/ml-chrome/background.js"),"utf8");
const start=file.indexOf("function searchCards(){");
const stop=file.indexOf("\nfunction productDetails(){",start);
assert(start>=0 && stop>start);

function fake(anchorData, {hasKnownCard=true}={}){
  const anchors=anchorData.map(({url,title})=>({
    href:url,
    textContent:title,
    closest:()=>null,
  }));
  const card={
    querySelectorAll:()=>anchors,
    querySelector:()=>({textContent:"SSD NVMe de 1TB"}),
  };
  const doc={
    readyState:"complete",
    title:"Notebook | Mercado Livre",
    body:{innerText:"Resultados. Mais relevantes"},
    querySelector:()=>({textContent:"Notebooks disponíveis"}),
    querySelectorAll:(selector)=>{
      if(selector.includes("li.ui-search-layout__item"))return hasKnownCard?[card]:[];
      if(selector==="a[href]")return anchors;
      throw Error("Unexpected selector "+selector);
    },
  };
  return doc;
}
function execute(doc){
  const code=file.slice(start,stop)+"\nsearchCards();";
  return vm.runInNewContext(code,{document:doc,URL,location:{
    hostname:"lista.mercadolivre.com.br",pathname:"/notebook"
  }}, {timeout:2000});
}
const goodUrl="https://www.mercadolivre.com.br/ssd-nvme-hikvision/p/MLB24117280?tracking_id=abc";
{
  const result=execute(fake([{url:goodUrl,title:"SSD NVMe Hikvision 1TB"}]));
  assert.equal(result.cards.length,1);
  assert.equal(result.cards[0].url,
    "https://www.mercadolivre.com.br/ssd-nvme-hikvision/p/MLB24117280");
  assert.equal(result.diagnostics.results,1);
  assert.equal(result.diagnostics.layoutCards,1);
}
{
  const result=execute(fake([{url:goodUrl,title:"SSD NVMe Hikvision 1TB"}], {hasKnownCard:false}));
  assert.equal(result.cards.length,1);
  assert.equal(result.diagnostics.layoutCards,0);
}
{
  const result=execute(fake([
    {url:"https://malicious.test/p/MLB24117280",title:"SSD NVMe Hikvision 1TB"},
    {url:"https://www.mercadolivre.com.br/l/cupom",title:"SSD NVMe Hikvision 1TB"},
  ]));
  assert.equal(result.cards.length,0);
  assert.equal(result.diagnostics.anchors,2);
  assert.equal(result.diagnostics.productLinks,0);
}
{
  const result=execute(fake([]));
  assert.equal(result.cards.length,0);
  assert.equal(result.diagnostics.layoutCards,1);
  assert.equal(result.diagnostics.anchors,0);
}

{
  const result=execute(fake([{url:goodUrl,title:"SSD NVMe Hikvision 1TB"}]));
  assert.equal(result.diagnostics.pageHost,"lista.mercadolivre.com.br");
  assert.equal(result.diagnostics.pagePath,"/notebook");
  assert.equal(result.diagnostics.pageTitle,"Notebook | Mercado Livre");
  assert.equal(result.diagnostics.hasResultsText,true);
  assert.equal(result.diagnostics.sampleLinks.length,1);
  assert.equal(result.diagnostics.sampleLinks[0],
    "www.mercadolivre.com.br/ssd-nvme-hikvision/p/MLB24117280");
}
process.stdout.write("searchCards DOM tests OK\n");
