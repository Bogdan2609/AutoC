/* v0.16 — anonymous read-only quote preview, never stores credentials. */
(()=>{'use strict';
const money=v=>new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB'}).format(Number(v));
const mk=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
for(const form of document.querySelectorAll('.quote-preview-form')){
 form.addEventListener('submit',async event=>{
  event.preventDefault();const out=form.querySelector('.quote-preview-result');const btn=form.querySelector('button');
  out.replaceChildren(mk('small','Проверяем актуальный снимок…'));btn.disabled=true;
  try {
   const body={snapshot_id:form.dataset.snapshotId,supplier_account_id:form.dataset.accountId,
     supplier_destination_id:form.dataset.destinationId,quantity:Number(form.elements.quantity.value),
     pickup_node_id:form.dataset.pickupId||null,route_node_ids:JSON.parse(form.dataset.routeNodeIds||'[]')};
   const response=await fetch('/api/v1/marketplace/products/'+encodeURIComponent(form.dataset.productId)+'/offers/preview',
       {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'});
   if(!response.ok){let msg='Условия изменились, обновите страницу.';try{const j=await response.json();if(typeof j.detail==='string')msg=j.detail;}catch{}throw Error(msg)}
   const q=await response.json();const box=document.createElement('div');box.className='quote-result-summary';
   box.append(mk('strong','Товар: '+money(q.goods_subtotal)),mk('span','Доставка от нашей точки до ПВЗ: '+money(q.merchant_delivery_fee)),
     mk('strong','Известная часть стоимости: '+money(q.known_components_total)));
   if(q.estimated_days!==null)box.append(mk('span','Ориентир по сроку: '+q.estimated_days+' дн.'));
   box.append(mk('small',q.pickup_selected?'ПВЗ выбран.':'ПВЗ не выбран; расчёт до нашей точки приёмки.'));
   box.append(mk('small',q.supplier_delivery_fee_known?'':'Доставка со склада поставщика: тариф неизвестен. ИТОГО не рассчитано.'));
   box.append(mk('small','Это предварительный расчёт без резерва. Покупка и оплата недоступны.'));
   out.replaceChildren(box);
  }catch(err){out.replaceChildren(mk('small',err.message||'Ошибка расчёта'));out.classList.add('quote-error');}
  finally{btn.disabled=false;}
 });
}
})();
