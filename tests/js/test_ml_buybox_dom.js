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
  values=[302500],priceContext="R$ 3.025,00 no Pix",original=null,
  paymentContext="",shippingContext="",shippingHidden=false,buyboxContext=""}={}){
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
      if(selector===".ui-pdp-price__subtitles"&&paymentContext)
        return [{innerText:paymentContext,getClientRects:()=>[1]}];
      if(selector===".ui-pdp-shipping"&&shippingContext)
        return [{innerText:shippingContext,getClientRects:()=>shippingHidden?[]:[1]}];
      if(selector===".ui-pdp-buybox"&&buyboxContext)
        return [{innerText:buyboxContext,getClientRects:()=>[1]}];
      if(selector.includes("__second-line"))return prices;
      if(selector.includes("andes-money-amount"))return [...prices];
      return [];
    }
  };
}
function check(doc){
  // Match chrome.scripting.executeScript({func: productDetails}): the browser
  // gets ONLY the function serialization, never sibling helper declarations.
  const pageGlobals={
    document:doc,
    location:{origin:"https://www.mercadolivre.com.br",pathname:"/notebook/p/MLB24117280"},
    getComputedStyle:()=>({textDecorationLine:"none"}),
  };
  return vm.runInNewContext("("+src.slice(from,to).trim()+")()",pageGlobals,{timeout:2000});
}

const invalidDom = {
  querySelector:()=>{throw Error("Protected page DOM");},
  querySelectorAll:()=>[],
};
assert.equal(check(invalidDom).status,"PRODUCT_INSPECTION_ERROR");

const valid=check(fakePage({values:[302500],original:526500,
  priceContext:"R$ 5.265,00\nR$ 3.025,00 no Pix\n10x R$ 342,30 sem juros"}));
assert.equal(valid.status,"VALIDATED");
assert.equal(valid.price_cents,302500);
assert.equal(valid.price_payment_method,"PIX");
assert.equal(valid.price_evidence,"VISIBLE_DOM_PRICE_V2");
assert.equal(valid.buy_now_enabled,true);
assert.equal(valid.installment_count,10);
assert.equal(valid.installment_cents,34230);
assert.equal(valid.installment_interest_free,true);
assert.equal(valid.installment_with_interest,false);


const standard=check(fakePage({values:[390000],
  priceContext:"R$ 3.900,00\n10x R$ 390,00 sem juros",button:"Comprar agora"}));
assert.equal(standard.status,"VALIDATED");
assert.equal(standard.price_cents,390000);
assert.equal(standard.price_payment_method,"STANDARD");
assert.equal(standard.installment_count,10);
assert.equal(standard.installment_cents,39000);
assert.equal(standard.installment_interest_free,true);

const noCard=check(fakePage({priceContext:"R$ 3.025,00 no Pix"}));
assert.equal(noCard.status,"VALIDATED");
assert.equal(noCard.installment_count,null);

const falseCard=check(fakePage({priceContext:"R$ 3.025,00 no Pix\n10x R$ 12,30 sem juros"}));
assert.equal(falseCard.status,"VALIDATED");
assert.equal(falseCard.installment_count,null);

const pricedWithInterest=check(fakePage({
  values:[302500], priceContext:"R$ 3.025,00\n10x de R$ 342,30 com juros"
}));
assert.equal(pricedWithInterest.installment_count,10);
assert.equal(pricedWithInterest.installment_interest_free,false);
assert.equal(pricedWithInterest.installment_with_interest,true);


// Multiple payment options: choose lowest *interest-free* monthly amount,
// even when an interest-bearing plan advertises more installments.
const mixed=check(fakePage({
  values:[120000],
  priceContext:"R$ 1.200,00\n6x de R$ 200,00 sem juros\n12x de R$ 100,00 sem juros\n24x de R$ 74,90 com juros"
}));
assert.equal(mixed.status,"VALIDATED");
assert.equal(mixed.installment_count,12);
assert.equal(mixed.installment_cents,10000);
assert.equal(mixed.installment_interest_free,true);
assert.equal(mixed.installment_with_interest,false);

// Do not assume the largest count wins when its monthly payment is higher.
const lowestFreePayment=check(fakePage({
  values:[120000],
  priceContext:"R$ 1.200,00\n12x de R$ 120,00 sem juros\n10x de R$ 120,00 sem juros\n6x de R$ 200,00 sem juros"
}));
assert.equal(lowestFreePayment.installment_count,12);
assert.equal(lowestFreePayment.installment_cents,12000);
assert.equal(lowestFreePayment.installment_interest_free,true);

const interestOnly=check(fakePage({
  values:[120000],
  priceContext:"R$ 1.200,00\n6x R$ 231,00 com juros\n12x de R$ 120,00 com juros"
}));
assert.equal(interestOnly.installment_count,12);
assert.equal(interestOnly.installment_cents,12000);
assert.equal(interestOnly.installment_interest_free,false);
assert.equal(interestOnly.installment_with_interest,true);

const unlabelled=check(fakePage({
  values:[120000],
  priceContext:"R$ 1.200,00\n12x de R$ 100,00"
}));
assert.equal(unlabelled.installment_count,null);
assert.equal(unlabelled.installment_interest_free,null);

// Distinct prices for the same 12x no-interest plan are ambiguous; choose
// another unambiguous verified plan, never advertise the questionable 12x.
const conflictingFree=check(fakePage({
  values:[120000],
  priceContext:"R$ 1.200,00\n12x de R$ 100,00 sem juros\n12x de R$ 115,00 sem juros\n6x de R$ 200,00 sem juros"
}));
assert.equal(conflictingFree.installment_count,6);
assert.equal(conflictingFree.installment_cents,20000);
assert.equal(conflictingFree.installment_interest_free,true);

const misleading=check(fakePage({
  values:[120000],
  priceContext:"R$ 1.200,00\n24x de R$ 10,00 sem juros\n12x de R$ 120,00 com juros"
}));
assert.equal(misleading.installment_count,12);
assert.equal(misleading.installment_with_interest,true);

// Real layout: the visible price box contains only the price, while card
// financing and shipping are in sibling buy-box elements.
const alienware=check(fakePage({
  title:"Monitor Gamer Dell Alienware 25 IPS 320Hz G-Sync Pivot",
  values:[172796], priceContext:"R$ 1.727,96",
  paymentContext:"10x R$ 172,80 sem juros",
  shippingContext:"Chegará grátis amanhã\n⚡ FULL"
}));
assert.equal(alienware.status,"VALIDATED");
assert.equal(alienware.price_cents,172796);
assert.equal(alienware.price_payment_method,"STANDARD");
assert.equal(alienware.installment_count,10);
assert.equal(alienware.installment_cents,17280);
assert.equal(alienware.installment_interest_free,true);
assert.equal(alienware.shipping_evidence,"VISIBLE_DOM_SHIPPING_V1");
assert.equal(alienware.shipping_free,true);
assert.equal(alienware.shipping_tomorrow,true);
assert.equal(alienware.shipping_full,true);

const hiddenShipping=check(fakePage({
  values:[172796],priceContext:"R$ 1.727,96",
  shippingContext:"Chegará grátis amanhã\n⚡ FULL",shippingHidden:true,
}));
assert.equal(hiddenShipping.shipping_evidence,null);
assert.equal(hiddenShipping.shipping_free,false);

const unrelatedPayment=check(fakePage({
  values:[172796],priceContext:"R$ 1.727,96",
  buyboxContext:"10x de R$ 172,80 sem juros\nFrete grátis anunciado"
}));
assert.equal(unrelatedPayment.installment_count,10);
assert.equal(unrelatedPayment.shipping_free,true);

const conditionalFree=check(fakePage({
  values:[172796],priceContext:"R$ 1.727,96",
  shippingContext:"Frete grátis em compras acima de R$ 199"
}));
assert.equal(conditionalFree.shipping_free,false);
assert.equal(conditionalFree.shipping_evidence,null);

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
