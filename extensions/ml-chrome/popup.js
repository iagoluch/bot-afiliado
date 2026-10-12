"use strict";
const status = document.getElementById("status");
const enabled = document.getElementById("enabled");
const once = document.getElementById("once");
const register = document.getElementById("register");
const send = (payload) => chrome.runtime.sendMessage(payload);
async function refresh(){
  try{
    const info=await send({action:"status"});
    enabled.checked=!!info.enabled;
    status.textContent="Ponte local: "+info.bridge+"\nGerador: "+(info.generatorUrl?"configurado":"não configurado")+
      "\nÚltima execução: "+info.lastStatus+(info.lastDetail?"\n"+info.lastDetail:"");
  }catch(_e){status.textContent="Não foi possível conectar ao serviço da extensão.";}
}
register.addEventListener("click", async()=>{
  register.disabled=true;
  const result=await send({action:"setGenerator"}).catch(()=>({status:"ERRO"}));
  status.textContent=result.status;
  register.disabled=false;await refresh();
});
once.addEventListener("click",async()=>{
  once.disabled=true;
  status.textContent="Executando uma busca; mantenha o Chrome aberto...";
  const result=await send({action:"runOnce"}).catch(()=>({status:"ERRO"}));
  status.textContent=JSON.stringify(result,null,2);
  once.disabled=false;
});
enabled.addEventListener("change",async()=>{
  const result=await send({action:"enable",enabled:enabled.checked}).catch(()=>({status:"ERRO"}));
  status.textContent=result.status;
  if(!result.status.startsWith("AUTOMATICO_"))await refresh();
});
refresh();
