"use strict";
/* Operates only visible DOM in ordinary authenticated Chrome, no cookies/API. */
const HOST="br.iagotech.botafiliado", ALARM="ml-tech-cycle";
const WORDS=["ssd nvme","fone bluetooth","notebook","mouse gamer","teclado mecanico","monitor gamer","celular","memoria ram","tablet","carregador usb c"];
let busy=false;
function official(url) {
  try {
    const u=new URL(url);
    return u.protocol==="https:" && ["www.mercadolivre.com.br","mercadolivre.com.br","produto.mercadolivre.com.br","lista.mercadolivre.com.br"].includes(u.hostname);
  } catch (_e) {return false;}
}
function productUrl(url) {
  if(!official(url)) return null;
  const u=new URL(url);
  if(!/(?:\/p\/MLB\d{5,16}(?:\/|$))|(?:MLB-\d{5,16})/i.test(u.pathname))return null;
  return u.origin+u.pathname.replace(/\/$/,"");
}
async function native(message){
  try {return await chrome.runtime.sendNativeMessage(HOST,message);}
  catch(_e){throw Error("PONTE_LOCAL_NAO_CONFIGURADA");}
}
async function waitTab(id,ms=25000){
  return await new Promise(async(resolve,reject)=>{
    let done=false;
    const finish=(err)=>{
      if(done)return;done=true;clearTimeout(timer);chrome.tabs.onUpdated.removeListener(listener);
      if(err)reject(Error(err));else resolve();
    };
    const listener=(tabId,info)=>{if(tabId===id&&info.status==="complete")finish();};
    chrome.tabs.onUpdated.addListener(listener);
    const timer=setTimeout(()=>finish("TEMPO_ESGOTADO"),ms);
    try{if((await chrome.tabs.get(id)).status==="complete")finish();}
    catch(_e){finish("ABA_FECHADA");}
  });
}
async function ensurePage(id){
  const tab=await chrome.tabs.get(id);
  if(!official(tab.url)||/\/login\/|\/registration\//i.test(tab.url))throw Error("SESSAO_NAO_AUTENTICADA");
}
async function navigate(id,url){
  await chrome.tabs.update(id,{url});await waitTab(id);await ensurePage(id);
}
async function exec(id,func,...args){
  return (await chrome.scripting.executeScript({target:{tabId:id},func,args}))[0]?.result;
}
function searchCards(){
  // Search layouts have changed across Mercado Livre versions. Resolve
  // product links inside cards rather than relying on one title class.
  const cards=[...document.querySelectorAll(
    "li.ui-search-layout__item, .andes-card.poly-card, div.poly-card, article.poly-card")];
  const productPath=/(?:\/p\/MLB\d{5,16}(?:\/|$))|(?:MLB-\d{5,16}(?:\/|$|[-_]))/i;
  const seen=new Set(), results=[];
  let productLinkCount=0;
  const append=(anchor,card)=>{
    if(!anchor || !anchor.href)return;
    let parsed;
    try{parsed=new URL(anchor.href);}
    catch(_e){return;}
    if(parsed.protocol!=="https:" ||
      !["www.mercadolivre.com.br","mercadolivre.com.br","produto.mercadolivre.com.br"].includes(parsed.hostname) ||
      !productPath.test(parsed.pathname))return;
    productLinkCount++;
    const textFromAnchor=(anchor.textContent||"").trim().replace(/\s+/g," ");
    const titleFromCard=(card?.querySelector(
      "h2.poly-component__title-wrapper, h2, h3, a.poly-component__title")?.textContent||"").trim().replace(/\s+/g," ");
    const title=(textFromAnchor.length>=6 ? textFromAnchor : titleFromCard);
    const url=parsed.origin+parsed.pathname.replace(/\/$/,"");
    if(title.length<6||seen.has(url))return;
    seen.add(url);
    results.push({title:title.slice(0,250),url});
  };
  for(const card of cards.slice(0,80)){
    const links=[...card.querySelectorAll(
      "a.poly-component__title, a.ui-search-item__group__element, a.poly-card__title, a[href]")];
    for(const anchor of links.slice(0,35))append(anchor,card);
  }
  // Alternative layouts may have linked titles outside the known cards.
  if(results.length===0){
    for(const anchor of [...document.querySelectorAll("a[href]")].slice(0,300)){
      append(anchor,anchor.closest("article, li, .poly-card, .ui-search-layout__item"));
    }
  }
  return {
    cards:results.slice(0,20),
    diagnostics:{
      layoutCards:cards.length,
      anchors:document.querySelectorAll("a[href]").length,
      productLinks:productLinkCount,
      results:results.length,
      readyState:document.readyState,
      pageHost:(typeof location!=="undefined"?location.hostname:"").slice(0,90),
      pagePath:(typeof location!=="undefined"?location.pathname:"").slice(0,160),
      pageTitle:String(document.title||"").slice(0,100),
      heading:String(document.querySelector?.("h1")?.textContent||"").trim().slice(0,100),
      hasResultsText:/\bresultados\b|mais relevantes/i.test(document.body?.innerText||""),
      hasAccessWarning:/limite de tentativas|verifique sua identidade|não sou um robô|captcha|acesso negado/i.test(document.body?.innerText||""),
      sampleLinks:[...document.querySelectorAll("a[href]")].slice(0,150).map(a=>{
        try{
          const u=new URL(a.href);
          return (u.protocol==="https:" ? u.hostname+u.pathname.slice(0,105) : "");
        }catch(_e){return "";}
      }).filter(x=>x && !x.startsWith("javascript:")).filter((x,i,arr)=>arr.indexOf(x)===i).slice(0,7)
    }
  };
}
function productDetails(){
  const title=(document.querySelector("h1.ui-pdp-title, h1.poly-component__title")?.textContent||"").trim();
  const amount=document.querySelector(".ui-pdp-price__main-container .andes-money-amount, [data-testid='price-part'] .andes-money-amount, .ui-pdp-price__second-line .andes-money-amount");
  if(!amount)return null;
  const whole=(amount.querySelector(".andes-money-amount__fraction")?.textContent||"").replace(/[.\s]/g,"");
  const cents=(amount.querySelector(".andes-money-amount__cents")?.textContent||"00").trim();
  if(!/^\d{1,8}$/.test(whole)||!/^\d{1,2}$/.test(cents))return null;
  const price=Number(whole)*100+Number(cents.padEnd(2,"0"));
  const buy=[...document.querySelectorAll("button,a")].some(el=>
    /^Comprar agora$/i.test((el.textContent||"").trim().replace(/\s+/g," "))&&
    el.getClientRects().length>0&&!el.disabled&&el.getAttribute("aria-disabled")!=="true");
  if(!buy||price<100||price>50000000||title.length<6)return null;
  return {title:title.slice(0,250),source_url:location.origin+location.pathname.replace(/\/$/,""),
    price_cents:price,buy_now_enabled:true};
}
function isGenerator(){
  return !!document.querySelector("textarea") &&
    [...document.querySelectorAll("button")].some(b=>/^Gerar$/i.test((b.textContent||"").trim())) &&
    /\btelegram\b/i.test(document.body.innerText||"");
}
async function generateLink(sourceUrl){
  const field=document.querySelector("textarea");
  const button=[...document.querySelectorAll("button")].find(b=>/^Gerar$/i.test((b.textContent||"").trim()));
  if(!field||!button||!/\btelegram\b/i.test(document.body.innerText||""))return {status:"GERADOR_NAO_LOCALIZADO"};
  const re=/https:\/\/(?:meli\.la\/[A-Za-z0-9]{3,80}|(?:www\.)?mercadolivre\.com\/sec\/[A-Za-z0-9_-]{3,100})(?![A-Za-z0-9_./?%#&=-])/ig;
  const links=()=>{
    const parts=[...document.querySelectorAll("a[href],input,textarea")].slice(0,250)
      .map(el=>el.getAttribute("href")||el.value||"").filter(x=>x.startsWith("https://"));
    parts.push((document.body.innerText||"").slice(0,20000));
    return [...new Set(parts.join("\n").match(re)||[])];
  };
  const prior=new Set(links());
  const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value")?.set;
  if(setter)setter.call(field,sourceUrl);else field.value=sourceUrl;
  field.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"insertText",data:sourceUrl}));
  field.dispatchEvent(new Event("change",{bubbles:true}));
  await new Promise(r=>setTimeout(r,500));
  if(button.disabled)return {status:"GERAR_DESABILITADO"};
  button.click();
  for(let i=0;i<30;i++){
    await new Promise(r=>setTimeout(r,500));
    const fresh=links().filter(x=>!prior.has(x));
    if(fresh.length===1)return {status:"LINK_GERADO",affiliate_url:fresh[0]};
    if(fresh.length>1)return {status:"MULTIPLOS_LINKS_AMBIGUOS"};
  }
  return {status:"LINK_NAO_ENCONTRADO"};
}
async function report(status,detail=""){
  await chrome.storage.local.set({lastStatus:status,lastDetail:detail,lastDiagnostics:null,lastAttempt:Date.now()});
}
async function runOnce(){
  if(busy)return {status:"OCUPADO"};
  busy=true;let tab=null;
  try{
    const cfg=await chrome.storage.local.get({generatorUrl:"",index:0});
    if(!official(cfg.generatorUrl))throw Error("CONFIGURE_O_GERADOR");
    if((await native({action:"ping"})).status!=="OK")throw Error("PONTE_LOCAL_FALHOU");
    const keyword=WORDS[(cfg.index||0)%WORDS.length];
    await chrome.storage.local.set({index:(cfg.index||0)+1});
    const url="https://lista.mercadolivre.com.br/"+encodeURIComponent(keyword).replace(/%20/g,"-");
    tab=(await chrome.tabs.create({url,active:false})).id;
    await waitTab(tab);await ensurePage(tab);
    let search={cards:[],diagnostics:{}};
    // A completed navigation does not mean that the asynchronous product
    // grid has finished rendering. Wait for visible product links, bounded.
    for(let attempt=0;attempt<9;attempt++){
      search=(await exec(tab,searchCards))||search;
      if(search.cards?.length)break;
      if(attempt<8)await new Promise(resolve=>setTimeout(resolve,1200));
    }
    const cards=search.cards||[];
    const searchDiag=search.diagnostics||{};
    if(cards.length===0){
      const reason=searchDiag.anchors===0?"BUSCA_SEM_CONTEUDO":
        searchDiag.productLinks===0?"BUSCA_SEM_LINKS_DE_PRODUTO":
        "BUSCA_SEM_TITULOS_DE_PRODUTO";
      await report(reason,"cards="+(searchDiag.layoutCards||0)+
        " | links="+(searchDiag.anchors||0)+" | produtos="+(searchDiag.productLinks||0));
      return {status:reason,queued:0,keyword,diagnostics:searchDiag};
    }
    let skipped=0;
    const reasons={url:0,product:0,generator:0,bridge:0};

    for(const item of cards.slice(0,6)){
      const source=productUrl(item.url);
      if(!source||seen.has(source)){skipped++;reasons.url++;continue;}
      seen.add(source);
      await navigate(tab,source);
      const product=await exec(tab,productDetails);
      if(!product||!productUrl(product.source_url)){skipped++;reasons.product++;continue;}
      await navigate(tab,cfg.generatorUrl);
      const generated=await exec(tab,generateLink,product.source_url);
      if(generated?.status!=="LINK_GERADO"){skipped++;reasons.generator++;continue;}
      const queued=await native({action:"queue",product,affiliate_url:generated.affiliate_url});
      if(queued?.status==="QUEUED_FOR_WORKER"){
        await report("FILA_CRIADA","Oferta validada entregue ao worker do Telegram");
        return {status:"FILA_CRIADA",queued:1,keyword,skipped};
      }
      skipped++;reasons.bridge++;
    }
    await report("SEM_OFERTA_APROVADA","encontrados="+cards.length+" | analisados="+skipped+
      " | produto="+reasons.product+" | gerador="+reasons.generator+" | ponte="+reasons.bridge);
    return {status:"SEM_OFERTA_APROVADA",queued:0,keyword,found:cards.length,skipped,reasons};
  }catch(error){
    const reason=(error?.message||"ERRO_DESCONHECIDO").slice(0,120);
    await report("PARADO",reason);
    return {status:"PARADO",reason};
  }finally{
    if(tab!==null)await chrome.tabs.remove(tab).catch(()=>{});
    busy=false;
  }
}
async function diagnoseSearch(){
  // One visible, user-initiated diagnostic tab. Does not generate a link,
  // talk to the native queue, or send anything to Telegram.
  let tab=null;
  try{
    const keyword="notebook";
    const url="https://lista.mercadolivre.com.br/"+keyword;
    tab=(await chrome.tabs.create({url,active:true})).id;
    await waitTab(tab);
    await ensurePage(tab);
    let search={cards:[],diagnostics:{}};
    for(let attempt=0;attempt<8;attempt++){
      search=(await exec(tab,searchCards))||search;
      if(search.cards?.length)break;
      if(attempt<7)await new Promise(done=>setTimeout(done,1200));
    }
    const result={
      status:search.cards?.length?"RESULTADOS_ENCONTRADOS":"PAGINA_SEM_PRODUTOS_RECONHECIDOS",
      keyword,found:search.cards?.length||0,
      diagnostics:search.diagnostics,
      instruction:"A aba de busca ficara aberta para inspecao. Nenhuma oferta foi enviada."
    };
    await chrome.storage.local.set({lastStatus:result.status,lastDetail:"Diagnostico de busca sem publicacao",lastDiagnostics:result});
    return result;
  }catch(error){
    const result={status:"DIAGNOSTICO_FALHOU",reason:String(error?.message||"ERRO").slice(0,100)};
    await chrome.storage.local.set({lastStatus:result.status,lastDetail:result.reason,lastDiagnostics:result});
    return result;
  }
}
async function resetAlarm(){
  await chrome.alarms.clear(ALARM);
  const data=await chrome.storage.local.get({enabled:false,generatorUrl:""});
  if(data.enabled&&data.generatorUrl)await chrome.alarms.create(ALARM,{delayInMinutes:360,periodInMinutes:360});
}
chrome.runtime.onInstalled.addListener(async()=>{
  await chrome.storage.local.set({enabled:false,lastStatus:"DESATIVADO"});
  await resetAlarm();
});
chrome.runtime.onStartup.addListener(resetAlarm);
chrome.alarms.onAlarm.addListener(alarm=>{
  if(alarm.name===ALARM)chrome.storage.local.get({enabled:false}).then(data=>{
    if(data.enabled)return runOnce();
  }).catch(()=>{});
});
chrome.runtime.onMessage.addListener((msg,_sender,reply)=>{
  (async()=>{
    if(!msg||typeof msg!=="object")return {status:"ACAO_INVALIDA"};
    if(msg.action==="status"){
      const cfg=await chrome.storage.local.get({enabled:false,generatorUrl:"",
        lastStatus:"AGUARDANDO_CONFIGURACAO",lastDetail:"",lastDiagnostics:null});
      let bridge="NAO_TESTADO";
      try{bridge=(await native({action:"ping"}))?.status||"INDISPONIVEL";}
      catch(_e){bridge="NAO_INSTALADA";}
      return {...cfg,bridge};
    }
    if(msg.action==="setGenerator"){
      const tabs=await chrome.tabs.query({active:true,currentWindow:true});
      const tab=tabs[0];
      if(!tab||!official(tab.url))return {status:"ABRA_O_GERADOR"};
      if(!(await exec(tab.id,isGenerator)))return {status:"GERADOR_OU_TELEGRAM_AUSENTE"};
      await chrome.storage.local.set({generatorUrl:tab.url,lastStatus:"GERADOR_CONFIGURADO"});
      return {status:"GERADOR_CONFIGURADO"};
    }
    if(msg.action==="enable"){
      const cfg=await chrome.storage.local.get({generatorUrl:""});
      if(!cfg.generatorUrl)return {status:"CONFIGURE_O_GERADOR"};
      await chrome.storage.local.set({enabled:msg.enabled===true});
      await resetAlarm();
      return {status:msg.enabled===true?"AUTOMATICO_ATIVADO":"AUTOMATICO_DESATIVADO"};
    }
    if(msg.action==="diagnoseSearch")return await diagnoseSearch();
    if(msg.action==="runOnce")return await runOnce();
    return {status:"ACAO_DESCONHECIDA"};
  })().then(reply).catch(()=>reply({status:"FALHA_EXTENSAO"}));
  return true;
});
