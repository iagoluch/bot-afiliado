"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");

const content=fs.readFileSync(path.join(__dirname,"../../extensions/ml-chrome/background.js"),"utf8");
const start=content.indexOf("async function runOnce(){");
const end=content.indexOf("async function diagnoseSearch(){",start);
assert(start>=0&&end>start,"runOnce must exist in real extension");

async function simulate({emptyInBackground=false,productAvailable=true,linkGenerated=true}={}){
  const state={active:false,searches:0,productReads:0,queueCalls:0,generatorCalls:0,
    removed:0,activated:0,stored:{},status:""};
  const cards=[{title:"Fone Bluetooth Dapon H02D",url:"https://www.mercadolivre.com.br/p/MLB24117280"}];
  const functions={
    searchCards(){},
    productDetails(){},
    isGenerator(){},
    generateLink(){}
  };
  const context={
    busy:false, WORDS:["fone bluetooth"], TOPIC_BY_QUERY:{"fone bluetooth":"audio"},
    setTimeout:fn=>fn(), // keeps fixture fast; bounded retry behavior still executes
    chrome:{
      storage:{local:{
        async get(){return {generatorUrl:"https://www.mercadolivre.com.br/afiliados/linkbuilder#hub",index:0};},
        async set(data){Object.assign(state.stored,data);}
      }},
      tabs:{
        async create({active}){state.active=active;return {id:7};},
        async update(id,changes){if(changes.active){state.active=true;state.activated++;}return {id};},
        async remove(){state.removed++;}
      }
    },
    official:()=>true,
    native:async msg=>{
      if(msg.action==="ping")return {status:"OK"};
      if(msg.action==="topicStatus"){assert.equal(msg.topic,"audio");return {status:"OK"};}
      state.queueCalls++;
      assert.equal(msg.action,"queue");
      assert.equal(msg.topic,"audio");
      assert.equal(msg.affiliate_url,"https://meli.la/2MWiFPG");
      assert.equal(msg.product.price_cents,7490);
      return {status:"QUEUED_FOR_WORKER"};
    },
    waitTab:async()=>{},
    ensurePage:async()=>{},
    navigate:async()=>{},
    searchCards:functions.searchCards,
    productDetails:functions.productDetails,
    isGenerator:functions.isGenerator,
    generateLink:functions.generateLink,
    productUrl:value=>/^https:\/\/www\.mercadolivre\.com\.br\/p\/MLB24117280$/.test(value)?value:null,
    exec:async(_tab,func)=>{
      if(func===functions.searchCards){
        state.searches++;
        const found=!emptyInBackground||state.active;
        return {cards:found?cards:[],diagnostics:{anchors:found?600:145,productLinks:found?52:0,layoutCards:found?136:0}};
      }
      if(func===functions.productDetails){
        state.productReads++;
        return productAvailable?{status:"VALIDATED",title:"Fone Bluetooth Dapon H02D",
          price_cents:7490,price_evidence:"VISIBLE_DOM_PRICE_V2",
          price_payment_method:"STANDARD",other_price_cents:null,
          buy_now_enabled:true,
          source_url:"https://www.mercadolivre.com.br/p/MLB24117280"}:{status:"PURCHASE_CONTROL_MISSING"};
      }
      if(func===functions.isGenerator)return true;
      if(func===functions.generateLink){
        state.generatorCalls++;
        return linkGenerated?{status:"LINK_GERADO",affiliate_url:"https://meli.la/2MWiFPG"}:{status:"LINK_NAO_ENCONTRADO"};
      }
      throw Error("Unknown injected function");
    },
    report:async(status)=>{state.status=status;}
  };
  const program=content.slice(start,end)+"\nrunOnce();";
  const result=await vm.runInNewContext(program,context,{timeout:3000});
  return {result,state};
}

(async()=>{
  const base=await simulate();
  assert.equal(base.result.status,"FILA_CRIADA",JSON.stringify(base.result));
  assert.equal(base.state.queueCalls,1);
  assert.equal(base.state.generatorCalls,1);
  assert.equal(base.state.removed,1);
  assert.equal(base.state.activated,0);

  const fallback=await simulate({emptyInBackground:true});
  assert.equal(fallback.result.status,"FILA_CRIADA",JSON.stringify(fallback.result));
  assert.equal(fallback.result.foregroundFallback,true);
  assert.equal(fallback.state.activated,1);
  assert.equal(fallback.state.queueCalls,1);

  const unavailable=await simulate({productAvailable:false});
  assert.equal(unavailable.result.status,"SEM_OFERTA_APROVADA");
  assert.equal(unavailable.result.reasons.product,1);
  assert.equal(unavailable.result.productFailures.PURCHASE_CONTROL_MISSING,1);
  assert.equal(unavailable.state.queueCalls,0);

  const noAffiliate=await simulate({linkGenerated:false});
  assert.equal(noAffiliate.result.status,"SEM_OFERTA_APROVADA");
  assert.equal(noAffiliate.result.generatorFailure,"LINK_NAO_ENCONTRADO");
  assert.equal(noAffiliate.state.queueCalls,0);

  process.stdout.write("runOnce product->affiliate->native queue cases OK\n");
})().catch(error=>{console.error(error);process.exitCode=1;});
