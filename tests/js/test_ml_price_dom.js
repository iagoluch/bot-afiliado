"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const script=fs.readFileSync(path.join(__dirname,"../../extensions/ml-chrome/background.js"),"utf8");
const begin=script.indexOf("function extractVisibleProductPrice(){");
const end=script.indexOf("\nfunction productDetails(){",begin);
assert(begin>=0&&end>begin,"actual production price extractor missing");
const fn=script.slice(begin,end)+"\nextractVisibleProductPrice();";

function amount(value,{previous=false, visible=true, near=""}={}){
  const cents=String(value%100).padStart(2,"0");
  const whole=Math.floor(value/100).toLocaleString("pt-BR");
  return {
    previous, near,
    classList:{contains:()=>previous},
    closest(selector){return previous?{tagName:"S"}:null;},
    getClientRects(){return visible?[1]:[];},
    querySelector(selector){
      if(selector===".andes-money-amount__fraction")return {textContent:whole};
      if(selector===".andes-money-amount__cents")return {textContent:cents};
      return null;
    },
    parentElement:{parentElement:{innerText:near}}
  };
}
function extract(prices, context){
  const root={innerText:context, textContent:context};
  const document={
    querySelectorAll:(selector)=>selector.includes(".andes-money-amount")?prices:[],
    querySelector:(selector)=>selector.includes("ui-pdp-price")?root:null,
  };
  return vm.runInNewContext(fn,{document,getComputedStyle:()=>({textDecorationLine:"none"})},{timeout:2000});
}
{
  // Discounted current amount must win over old crossed-out R$900.
  const out=extract([amount(90000,{previous:true}),amount(75900)],"De R$ 900 por R$ 759");
  assert.equal(out.status,"VERIFIED");assert.equal(out.price_cents,75900);
  assert.equal(out.price_payment_method,"STANDARD");
  assert.equal(out.other_price_cents,null);
}
{
  const out=extract([amount(75900),amount(90000)],"R$ 759,00 no Pix ou R$ 900,00 em outros meios");
  assert.equal(out.status,"VERIFIED");assert.equal(out.price_cents,75900);
  assert.equal(out.price_payment_method,"PIX");
  assert.equal(out.other_price_cents,90000);
}
{
  const out=extract([amount(90000),amount(75900)],"Preço 900 e 759 sem condição identificada");
  assert.equal(out.status,"AMBIGUOUS_PAYMENT_PRICES");
}
{
  const out=extract([amount(90000,{previous:true})],"Preço riscado R$900");
  assert.equal(out.status,"NO_CURRENT_PRICE");
}
{
  const out=extract([amount(75900,{visible:false})],"R$ 759");
  assert.equal(out.status,"NO_CURRENT_PRICE");
}
{
  const out=extract([amount(75900)],"R$ 759,00");
  assert.equal(out.status,"VERIFIED");
  assert.equal(out.price_payment_method,"STANDARD");
}
{
  const out=extract([amount(90000),amount(75900),amount(70000)],"R$ 700 no Pix ou R$ 900");
  assert.equal(out.status,"AMBIGUOUS_MULTIPLE_PRICES");
}
process.stdout.write("ML visible current-price scenarios OK\n");
