"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const src=fs.readFileSync(path.join(__dirname,"../../extensions/ml-chrome/background.js"),"utf8");
const from=src.indexOf("function productDetails(){");
const to=src.indexOf("\nfunction isGenerator(){",from);
assert(from>=0&&to>from);

function amount(value,{old=false,visible=true}={}){
  const div={
    getClientRects:()=>visible?[1]:[],
    closest:()=>old?{tagName:"DEL"}:null,
    parentElement:null,
    querySelector(selector){
      const t=selector.includes("fraction")?String(Math.floor(value/100)):String(value%100).padStart(2,"0");
      return {textContent:t};
    }
  };
  return div;
}
function fakePage({title="Notebook 15 polegadas",button="Adicionar ao carrinho",disabled=false,
  values=[302500],priceContext="R$ 3.025,00 no Pix",original=null}={}){
  const prices=values.map(v=>amount(v));
  if(original!==null)prices.unshift(amount(original,{old:true}));
  const cta={textContent:button,value:"",getAttribute:()=>null,disabled,
    getClientRects:()=>[1]};
  const box={innerText:priceContext};
  return {
    querySelector(selector){
      if(selector.startsWith("h1"))return {textContent:title};
      if(selector.includes("ui-pdp-price"))return box;
      return null;
    },
    querySelectorAll(selector){
      if(selector.startsWith("button"))return [cta];
      if(selector.includes("__second-line"))return prices;
      if(selector.includes("andes-money-amount"))return [...prices];
      return [];
    }
  };
}
function check(doc){
  return vm.runInNewContext(src.slice(from,to)+"\nproductDetails();",{
    document:doc,
    location:{origin:"https://www.mercadolivre.com.br",pathname:"/notebook/p/MLB24117280"},
    getComputedStyle:()=>({textDecorationLine:"none"}),
  },{timeout:2000});
}

const valid=check(fakePage({values:[302500],original:526500,
  priceContext:"R$ 5.265,00\nR$ 3.025,00 no Pix\n10x R$ 342,30 sem juros"}));
assert.equal(valid.status,"VALIDATED");
assert.equal(valid.price_cents,302500);
assert.equal(valid.price_payment_method,"PIX");
assert.equal(valid.price_evidence,"VISIBLE_DOM_PRICE_V2");
assert.equal(valid.buy_now_enabled,true);

const standard=check(fakePage({values:[390000],
  priceContext:"R$ 3.900,00\n10x R$ 390,00 sem juros",button:"Comprar agora"}));
assert.equal(standard.status,"VALIDATED");
assert.equal(standard.price_cents,390000);
assert.equal(standard.price_payment_method,"STANDARD");

const noBuy=check(fakePage({button:"Comprar indisponível"}));
assert.equal(noBuy.status,"PURCHASE_CONTROL_MISSING");
const disabled=check(fakePage({disabled:true}));
assert.equal(disabled.status,"PURCHASE_CONTROL_MISSING");

const ambiguous=check(fakePage({values:[302500,320000],priceContext:"R$ 3025,00 e R$ 3200,00"}));
assert.equal(ambiguous.status,"AMBIGUOUS_PAYMENT_PRICES");

const three=check(fakePage({values:[300000,320000,360000],priceContext:"R$ 3000,00 no Pix"}));
assert.equal(three.status,"AMBIGUOUS_MULTIPLE_PRICES");

const better=check(fakePage({values:[90000],priceContext:"R$ 900,00 em outros meios\nR$ 759,00 no Pix"}));
assert.equal(better.status,"UNPARSED_LOWER_VISIBLE_PRICE");

process.stdout.write("ML buy-box product validation cases OK\n");
