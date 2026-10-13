"use strict";
/* Operates only visible DOM in ordinary authenticated Chrome, no cookies/API. */
const HOST="br.iagotech.botafiliado", ALARM="ml-tech-cycle";
const TOPICS=[
  {topic:"ssd",query:"ssd nvme"},
  {topic:"audio",query:"fone bluetooth"},
  {topic:"notebook",query:"notebook"},
  {topic:"mouse",query:"mouse gamer"},
  {topic:"teclado",query:"teclado mecanico"},
  {topic:"monitor",query:"monitor gamer"},
  {topic:"celular",query:"celular"},
  {topic:"ram",query:"memoria ram ddr4"},
  {topic:"tablet",query:"tablet android"},
  {topic:"carregador",query:"carregador usb c"},
  {topic:"gpu",query:"placa de video"},
  {topic:"rede",query:"roteador wifi"},
  {topic:"webcam",query:"webcam"},
  {topic:"placa_mae",query:"placa mae"},
  {topic:"wearable",query:"smartwatch"},
  {topic:"console",query:"controle xbox"},
  {topic:"impressora",query:"impressora"},
  {topic:"pendrive",query:"pendrive"},
  {topic:"gabinete",query:"gabinete gamer"},
  {topic:"fonte",query:"fonte atx"},
  {topic:"smart_home",query:"tomada inteligente"},
  {topic:"armazenamento",query:"hd externo"},
  {topic:"cpu",query:"processador ryzen"},
  {topic:"microfone",query:"microfone usb"}
];
const WORDS=TOPICS.map(x=>x.query);
const TOPIC_BY_QUERY=Object.fromEntries(TOPICS.map(x=>[x.query,x.topic]));
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
// Chrome serializes only the injected function, not its surrounding lexical
// scope. Keep ALL page-inspection helpers inside productDetails so the
// chrome.scripting.executeScript({func: productDetails}) call is self-contained.
function productDetails(){
  function extractVisibleProductPrice(){
    // Prefer the live buy-box price, not a historical amount, an installment,
    // or an unrelated recommendation. Mercado Livre uses multiple PDP layouts.
    const tiers=[
      ".ui-pdp-price__second-line .andes-money-amount",
      ".ui-pdp-price__main-container .andes-money-amount",
      "[data-testid='price-part'] .andes-money-amount",
      ".ui-pdp-price .andes-money-amount"
    ];
    const excludedSelector=[
      "s","del",".andes-money-amount--previous",
      ".ui-pdp-price__original-value","[class*='original-price']",
      "[class*='price-original']","[class*='installment']",
      "[class*='installments']"
    ].join(",");
    const readMoney=(node)=>{
      if(!node||node.getClientRects?.().length===0)return null;
      if(node.closest?.(excludedSelector))return null;
      try{
        let el=node;
        for(let i=0;i<3&&el;i++,el=el.parentElement){
          if(getComputedStyle(el).textDecorationLine?.includes("line-through"))return null;
        }
      }catch(_e){}
      const whole=(node.querySelector(".andes-money-amount__fraction")?.textContent||"").replace(/[.\s]/g,"");
      // A hidden cents span is not part of the advertised visible price.
      const centsNode=node.querySelector(".andes-money-amount__cents");
      const centsVisible=!centsNode?.getClientRects||
        centsNode.getClientRects().length>0;
      const part=(centsVisible?centsNode?.textContent:"00")?.trim()||"00";
      if(!/^\d{1,8}$/.test(whole)||!/^\d{1,2}$/.test(part))return null;
      const amount=Number(whole)*100+Number(part.padEnd(2,"0"));
      return Number.isSafeInteger(amount)&&amount>=100&&amount<=50000000?amount:null;
    };
    const buybox=document.querySelector(
      ".ui-pdp-price__main-container, .ui-pdp-price, [data-testid='price-part']"
    );
    if(!buybox)return {status:"PRICE_BOX_MISSING"};
    const context=String(buybox.innerText||buybox.textContent||"").slice(0,1800);
    const pixMention=/\bpix\b/i.test(context);
    const otherMention=/outros meios|outras formas|outros pagamentos/i.test(context);
    // Use first nonempty semantic tier rather than joining old prices, credits
    // and installments from several unrelated containers.
    let values=[],tierUsed="";
    for(const tier of tiers){
      const nodes=[...document.querySelectorAll(tier)];
      const amounts=[...new Set(nodes.map(readMoney).filter(x=>x!==null))].sort((a,b)=>a-b);
      if(amounts.length){
        values=amounts;tierUsed=tier;break;
      }
    }
    if(!values.length)return {status:"NO_CURRENT_PRICE"};
    if(values.length>2)return {status:"AMBIGUOUS_MULTIPLE_PRICES"};
    if(values.length===2&&!pixMention)return {status:"AMBIGUOUS_PAYMENT_PRICES"};
    // Validate text in the buy box. If there is a cheaper advertised value
    // not captured by the trusted price elements, do not invent its context.
    const mainPriceLines=context.split(/[\r\n]+/).filter(line=>
      !/\b\d+\s*x\b|parcelas?|sem juros|ao m[eê]s|por m[eê]s/i.test(line));
    const textAmounts=[...mainPriceLines.join("\n").matchAll(/(?:R\$\s*)?([0-9]{1,8}(?:\.[0-9]{3})*,[0-9]{2})(?!\d)/g)]
      .map(m=>{
        const [whole,cents]=m[1].replace(/\./g,"").split(",");
        return Number(whole)*100+Number(cents);
      }).filter(Number.isSafeInteger);
    if(textAmounts.some(x=>x>=100&&x<values[0])){
      return {status:"UNPARSED_LOWER_VISIBLE_PRICE"};
    }
    // Price with explicit Pix condition is only publishable when the lowest
    // price was actually identified in the price region.
    const price=values[0];
    const alternative=values.length===2&&otherMention?values[1]:null;
    if(values.length===2&&pixMention&&!otherMention){
      return {status:"UNLABELED_ALTERNATIVE_PRICE"};
    }
    // Mercado Livre often renders the card installment OUTSIDE the primary
    // .ui-pdp-price block, in a subtitle/payment section of the SAME buy box.
    // Read only visible, scoped purchase-area DOM. Never scan recommended
    // products or the whole document body.
    const readScoped=(selectors,max=3500)=>{
      const texts=[];
      for(const selector of selectors){
        for(const node of [...document.querySelectorAll(selector)].slice(0,6)){
          if(!node.getClientRects?.().length)continue;
          const value=String(node.innerText||node.textContent||"").slice(0,1500).trim();
          if(value&&!texts.includes(value))texts.push(value);
        }
      }
      return texts.join("\n").slice(0,max);
    };
    // Newer PDP layouts may render the installments as an adjacent sibling
    // rather than under a known payment class. Inspect ONLY a few visible,
    // bounded ancestors of the verified price box; never document.body.
    // This catches actual 10x / sem juros text without reading unrelated
    // recommendation cards across the page.
    const nearby=[];
    for(let ancestor=buybox.parentElement,level=0;
      ancestor && level<5;ancestor=ancestor.parentElement,level++){
      if(ancestor===document.body||ancestor===document.documentElement)break;
      if(ancestor.getClientRects?.().length===0)continue;
      const visible=String(ancestor.innerText||"").trim();
      if(visible.length>0&&visible.length<=7000){
        nearby.push(visible);
        if(/\b(?:[2-9]|1\d|2[0-4])\s*x\s*(?:de\s*)?(?:R\$\s*)?\d/i.test(visible))break;
      }
    }
    const paymentContext=[context,readScoped([
      ".ui-pdp-price__subtitles", ".ui-pdp-price__subtitle",
      ".ui-pdp-price__part", ".ui-pdp-installments",
      ".ui-pdp-payment-methods", ".ui-pdp-buybox__price",
      ".ui-pdp-buybox__payments", "[data-testid='installments']",
      "[data-testid='payment-methods']", "[data-testid='buy-box']",
      ".ui-pdp-container__row--price", ".ui-pdp-buybox"
    ]),...nearby].filter(Boolean).join("\n").slice(0,11000);
    // Shipping is only read from a nearby explicit buy-box delivery section.
    // Phrases from ads/recommendation cards must never be treated as facts.
    let shippingContext=readScoped([
      ".ui-pdp-shipping", ".ui-pdp-delivery",
      ".ui-pdp-buybox__shipping", ".ui-pdp-buybox__shipping-info",
      "[data-testid='shipping-info']", "[data-testid='shipping-details']",
      "[data-testid='delivery-info']"
    ],2400);
    if(!shippingContext){
      shippingContext=readScoped([".ui-pdp-buybox"],1500)
        .split(/\r?\n/).map(s=>s.trim())
        .filter(s=>/(?:chegar[aá]|entrega|frete|envio|FULL)/i.test(s))
        .join("\n").slice(0,650);
    }
    const shippingLines=shippingContext.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
    const shippingFree=shippingLines.some(line=>
      /\bgr[aá]tis\b/i.test(line) &&
      /\b(?:chegar[aá]|entrega|frete|envio)/i.test(line) &&
      !/(?:a partir de|acima de|compras (?:de|acima))/i.test(line));
    const shippingTomorrow=shippingLines.some(line=>
      /\b(?:chegar[aá]|entrega|receba|receber).{0,35}\bamanh[aã](?:\s|$)/i.test(line) ||
      /\bamanh[aã](?:\s|$).{0,35}\b(?:chegar[aá]|entrega)/i.test(line));
    const shippingFull=shippingLines.some(line=>/\bFULL\b/i.test(line));
    const shippingEvidence=(shippingFree||shippingTomorrow||shippingFull)
      ?"VISIBLE_DOM_SHIPPING_V1":null;
    // Only explicitly labelled, visible plans are eligible. Prefer the
    // LOWEST monthly payment with no interest among verified plans; use an
    // explicitly interest-bearing plan only if no safe free plan exists.
    // Never calculate a made-up installment from the cash/Pix price.
    const plans=[];
    const installmentRe=/\b([2-9]|1\d|2[0-4])\s*x\s*(?:de\s*)?(?:R\$\s*)?([0-9]{1,8}(?:\.[0-9]{3})*,[0-9]{2})\b([^\r\n]{0,28})/gi;
    for(const match of paymentContext.matchAll(installmentRe)){
      const count=Number(match[1]);
      const [whole,cents]=match[2].replace(/\./g,"").split(",");
      const amount=Number(whole)*100+Number(cents);
      const total=count*amount;
      // Defensive bound for stale prices or unrelated recommendations.
      if(!Number.isSafeInteger(total)||amount<100||
        total<price*0.9||total>price*2.5)continue;
      const suffix=match[3]||"";
      const interestFree=/\bsem juros\b/i.test(suffix);
      const withInterest=/\bcom juros\b/i.test(suffix);
      if(interestFree===withInterest)continue; // Unlabelled or contradictory.
      plans.push({count,amount,interestFree,withInterest});
    }
    // Conflicting totals for the SAME count and interest class are not proof
    // of a specific installment amount: discard that ambiguous option.
    const safePlans=plans.filter(p=>!plans.some(other=>
      other!==p && other.count===p.count &&
      other.interestFree===p.interestFree && other.amount!==p.amount));
    const options=safePlans.some(p=>p.interestFree)
      ?safePlans.filter(p=>p.interestFree)
      :safePlans.filter(p=>p.withInterest);
    options.sort((a,b)=>a.amount-b.amount||b.count-a.count);
    const plan=options[0]||null;
    return {status:"VERIFIED",price_cents:price,
      price_payment_method:pixMention?"PIX":"STANDARD",
      other_price_cents:alternative,
      installment_count:plan?plan.count:null,
      installment_cents:plan?plan.amount:null,
      installment_interest_free:plan?plan.interestFree:null,
      installment_with_interest:plan?plan.withInterest:null,
      shipping_evidence:shippingEvidence,
      shipping_free:shippingFree,
      shipping_tomorrow:shippingTomorrow,
      shipping_full:shippingFull,
      price_evidence:"VISIBLE_DOM_PRICE_V2",
      price_source:tierUsed};
  }
  try {
    const title=(document.querySelector(
      "h1.ui-pdp-title, h1.poly-component__title, h1[data-testid='product-title']"
    )?.textContent||"").trim();
    if(title.length<6)return {status:"PRODUCT_TITLE_MISSING"};
    // Purchase CTA may be "Comprar agora" OR "Adicionar ao carrinho",
    // depending on category, buy-box layout and seller. Both are explicit
    // sellability signals; disabled/hidden controls never prove stock.
    const purchase=[...document.querySelectorAll("button, a, input[type='submit']")].some(el=>{
      const label=(el.getAttribute?.("aria-label")||el.value||el.textContent||"")
        .trim().replace(/\s+/g," ");
      const enabled=el.getClientRects?.().length>0&&!el.disabled &&
        el.getAttribute("aria-disabled")!=="true";
      return enabled&&/^(Comprar agora|Adicionar ao carrinho|Comprar)$/i.test(label);
    });
    if(!purchase)return {status:"PURCHASE_CONTROL_MISSING"};
    const price=extractVisibleProductPrice();
    if(price.status!=="VERIFIED")return {status:price.status};
    return {...price,status:"VALIDATED",title:title.slice(0,250),
      source_url:location.origin+location.pathname.replace(/\/$/,""),
      buy_now_enabled:true};
  } catch (_error) {
    // Never fail silently as PRODUCT_DOM_UNAVAILABLE. Do not expose private
    // page contents or session state in extension diagnostics.
    return {status:"PRODUCT_INSPECTION_ERROR"};
  }
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
    const topic=TOPIC_BY_QUERY[keyword];
    await chrome.storage.local.set({index:(cfg.index||0)+1});
    // Local SQLite is authoritative: avoid searching/generating links for a
    // category already queued or posted in the last 60 minutes.
    const topicStatus=await native({action:"topicStatus",topic});
    if(topicStatus?.status==="TOPIC_COOLDOWN"){
      await report("CATEGORIA_EM_ESPERA",topic+" ja teve uma oferta recente");
      return {status:"CATEGORIA_EM_ESPERA",topic,queued:0};
    }
    if(topicStatus?.status!=="OK")throw Error("VERIFICACAO_CATEGORIA_FALHOU");
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
    // O diagnostico no Acer encontrou 20 produtos com aba ATIVA, enquanto
    // a busca automatica em aba inativa nao encontrou nenhum. Em caso de
    // grade vazia, ativa UMA vez a mesma aba e repete a leitura sem novo login.
    let foregroundFallback=false;
    if(!(search.cards?.length)){
      await chrome.tabs.update(tab,{active:true});
      foregroundFallback=true;
      for(let attempt=0;attempt<8;attempt++){
        await new Promise(resolve=>setTimeout(resolve,1100));
        search=(await exec(tab,searchCards))||search;
        if(search.cards?.length)break;
      }
    }
    const cards=search.cards||[];
    const searchDiag=search.diagnostics||{};
    if(cards.length===0){
      const reason=searchDiag.anchors===0?"BUSCA_SEM_CONTEUDO":
        searchDiag.productLinks===0?"BUSCA_SEM_LINKS_DE_PRODUTO":
        "BUSCA_SEM_TITULOS_DE_PRODUTO";
      await report(reason,"cards="+(searchDiag.layoutCards||0)+
        " | links="+(searchDiag.anchors||0)+" | produtos="+(searchDiag.productLinks||0));
      return {status:reason,queued:0,keyword,foregroundFallback,diagnostics:searchDiag};
    }
    let skipped=0;
    const reasons={url:0,product:0,generator:0,bridge:0};
    const productFailures={};
    const seen=new Set(); // evita ReferenceError ao encontrar o primeiro produto
    let generatorFailure="", bridgeFailure="";

    for(const item of cards.slice(0,10)){
      const source=productUrl(item.url);
      if(!source||seen.has(source)){skipped++;reasons.url++;continue;}
      seen.add(source);
      await navigate(tab,source);
      let product=null;
      for(let attempt=0;attempt<6;attempt++){
        product=await exec(tab,productDetails);
        if(product?.status==="VALIDATED")break;
        // Chromium sometimes defers rendering the buy-box in an inactive
        // tab. Retry once in the foreground before rejecting an item.
        if(attempt===2&&!foregroundFallback){
          await chrome.tabs.update(tab,{active:true});
          foregroundFallback=true;
        }
        if(attempt<5)await new Promise(resolve=>setTimeout(resolve,850));
      }
      if(product?.status!=="VALIDATED"||!productUrl(product.source_url)){
        skipped++;reasons.product++;
        const failure=product?.status||"PRODUCT_DOM_UNAVAILABLE";
        productFailures[failure]=(productFailures[failure]||0)+1;
        continue;
      }
      await navigate(tab,cfg.generatorUrl);
      // O Gerador eh React; carregamento "complete" pode preceder o formulario.
      for(let attempt=0;attempt<5;attempt++){
        if(await exec(tab,isGenerator))break;
        if(attempt<4)await new Promise(resolve=>setTimeout(resolve,800));
      }
      const generated=await exec(tab,generateLink,product.source_url);
      if(generated?.status!=="LINK_GERADO"){skipped++;reasons.generator++;generatorFailure=generated?.status||"SEM_RESPOSTA";continue;}
      const queued=await native({action:"queue",product,topic,affiliate_url:generated.affiliate_url});
      if(queued?.status==="QUEUED_FOR_WORKER"){
        await report("FILA_CRIADA","Oferta validada entregue ao worker do Telegram");
        return {status:"FILA_CRIADA",queued:1,keyword,skipped,foregroundFallback};
      }
      skipped++;reasons.bridge++;bridgeFailure=String(queued?.status||"SEM_RESPOSTA").slice(0,80);
    }
    await report("SEM_OFERTA_APROVADA","encontrados="+cards.length+" | analisados="+skipped+
      " | produto="+reasons.product+" | gerador="+reasons.generator+" | ponte="+reasons.bridge+
      (generatorFailure?" | ultimoGerador="+generatorFailure:"")+
      (bridgeFailure?" | ultimaPonte="+bridgeFailure:""));
    await chrome.storage.local.set({lastDiagnostics:{status:"PRODUCT_VALIDATION",
      keyword,found:cards.length,examined:skipped,reasons,productFailures}});
    return {status:"SEM_OFERTA_APROVADA",queued:0,keyword,found:cards.length,skipped,
      reasons,productFailures,generatorFailure,bridgeFailure,foregroundFallback};
  }catch(error){
    const reason=(error?.message||"ERRO_DESCONHECIDO").slice(0,120);
    await report("PARADO",reason);
    return {status:"PARADO",reason};
  }finally{
    if(tab!==null)await chrome.tabs.remove(tab).catch(()=>{});
    busy=false;
  }
}
// Read-only, user-triggered evidence for real Mercado Livre payment layouts.
// Outputs only scoped selector counts and sanitized installment expressions.
// No raw page HTML, address, session data, cart, cookies, credentials or URLs.
function inspectFinancialRegions(){
  const selectors=[
    ".ui-pdp-price", ".ui-pdp-price__subtitles", ".ui-pdp-installments",
    ".ui-pdp-payment-methods", "[data-testid='buy-box']",
    "[data-testid='installments']", ".ui-pdp-buybox",
    ".ui-pdp-container__row--price", "[class*='installment']"
  ];
  const matched=[], regions=[];
  const re=/\b(?:[2-9]|1\d|2[0-4])\s*x\s*(?:de\s*)?(?:R\$\s*)?\d{1,7}(?:[.]\d{3})*,\d{2}\s*(?:sem|com)\s+juros/gi;
  for(const selector of selectors){
    const nodes=[...document.querySelectorAll(selector)].slice(0,5);
    let visible=0, signals=0;
    for(const node of nodes){
      if(!node.getClientRects?.().length)continue;
      visible++;
      const txt=String(node.innerText||"").slice(0,6000);
      if(/\b(?:juros|parcelas?|cart[aã]o)\b/i.test(txt))signals++;
      for(const part of txt.matchAll(re)){
        const term=part[0].replace(/\s+/g," ").slice(0,70);
        if(!matched.includes(term)&&matched.length<12)matched.push(term);
      }
    }
    regions.push({selector,total:nodes.length,visible,paymentSignals:signals});
  }
  return {status:"DIAGNOSTICO_FINANCEIRO",regions,verifiedExpressions:matched};
}
async function diagnoseProduct(){
  const tabs=await chrome.tabs.query({active:true,currentWindow:true});
  const tab=tabs[0];
  if(!tab||!productUrl(tab.url))return {status:"ABRA_UM_PRODUTO_DO_MERCADO_LIVRE"};
  try{
    const [facts, details]=await Promise.all([
      exec(tab.id,inspectFinancialRegions),exec(tab.id,productDetails)
    ]);
    return {
      status:"DIAGNOSTICO_FINANCEIRO",
      productStatus:details?.status||"SEM_RETORNO",
      priceCents:details?.price_cents??null,
      installmentsRead:details?.installment_count??null,
      interestFreeRead:details?.installment_interest_free??null,
      ...facts,
      instruction:"Somente diagnostico local, sem publicacao ou envio de HTML."
    };
  }catch(_e){return {status:"DIAGNOSTICO_PRODUTO_FALHOU"};}
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
  if(data.enabled&&data.generatorUrl)await chrome.alarms.create(ALARM,{delayInMinutes:5,periodInMinutes:5});
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
    if(msg.action==="diagnoseProduct")return await diagnoseProduct();
    if(msg.action==="runOnce")return await runOnce();
    return {status:"ACAO_DESCONHECIDA"};
  })().then(reply).catch(()=>reply({status:"FALHA_EXTENSAO"}));
  return true;
});
