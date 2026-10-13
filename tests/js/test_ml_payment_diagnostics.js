"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../../extensions/ml-chrome/background.js"),"utf8");
const begin=source.indexOf("function inspectFinancialRegions(){");
const end=source.indexOf("async function diagnoseProduct(){",begin);
assert(begin>=0&&end>begin);
const raw=source.slice(begin,end).trim();

const visible={
  innerText:"10x de R$ 348,93 sem juros\n" +
    "Entrega para Rua Particular 123 CEP 88500000\n" +
    "token=PRIVATEPASSWORD\n" +
    "4x R$ 910,00 com juros",
  getClientRects:()=>[1]
};
const hidden={
  innerText:"24x R$ 19,99 sem juros",
  getClientRects:()=>[]
};
const document={
  querySelectorAll(selector){
    if(selector===".ui-pdp-price")return [visible,hidden];
    if(selector==="[class*='installment']")return [visible];
    return [];
  }
};
const output=vm.runInNewContext(raw+";inspectFinancialRegions()",{document},{timeout:2000});
assert.equal(output.status,"DIAGNOSTICO_FINANCEIRO");
assert.deepEqual(Array.from(output.verifiedExpressions),[
  "10x de R$ 348,93 sem juros","4x R$ 910,00 com juros"
]);
const encoded=JSON.stringify(output);
assert(!encoded.includes("PRIVATEPASSWORD"));
assert(!encoded.includes("Rua Particular"));
assert(!encoded.includes("88500000"));
assert(!encoded.includes("24x R$ 19,99"));
assert(!encoded.includes("https://"));
assert.equal(output.regions.find(r=>r.selector===".ui-pdp-price").visible,1);
assert.equal(output.regions.find(r=>r.selector===".ui-pdp-price").paymentSignals,1);
process.stdout.write("Read-only sanitized payment diagnostics OK\n");
