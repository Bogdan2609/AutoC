(()=>{'use strict';
const $=(q,root=document)=>root.querySelector(q), $$=(q,root=document)=>[...root.querySelectorAll(q)];
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const mon='/api/v1/backoffice/monitor';
const wb='/api/v1/backoffice/workspace', mp='/api/v1/admin/marketplace', bo='/api/v1/backoffice', op=bo+'/operations', en=bo+'/offer-engine';
const titles={
'supplier-feeds':['Прайс-листы поставщиков','SUPPLIER FEEDS · v0.31.5','Загрузка, версия закупочных цен, сравнение, контроль активации'],
'catalog-automation':['Автоматизация каталога','CATALOG PIPELINE v0.31.3','Preflight, восстановление категорий и безопасное создание черновиков без публикации'],
analytics:['Аналитика бизнеса','ANALYTICS A3 · READ ONLY','Поставщики, история цен, экономика собственных и партнёрских предложений'],
finance:['Финансы и налоги','FINANCE F1','Юридические лица, налоговые профили и безопасный расчёт НДС'],
'finance-pricing':['Наценки и комиссии','FINANCE F2','Расчёт цен, себестоимости и комиссий в отдельном тестовом контуре'],
'finance-ledger':['Журнал и взаиморасчёты','FINANCE F3','Тестовые проводки, взаиморасчёты и сторнирование без движения денег'],
'finance-payments':['СБП, чеки и сверка','FINANCE F4 · SHADOW','Моделирование оплат, возвратов, планов чеков и сверки — без обращения в банк'],
'finance-registers':['Налоговые регистры и экспорт','FINANCE F5 · SHADOW','Тестовые книги доходов, НДС и агентских операций, предварительный CSV'],
'ce-brands':['Бренды','МАСТЕР-КАТАЛОГ','Производители, профили и нормализация'],
'ce-brand':['Профиль бренда','МАСТЕР-КАТАЛОГ','Описание и псевдонимы производителя'],
'ce-categories':['Категории','МАСТЕР-КАТАЛОГ','Иерархия, наследование и видимость'],
'ce-category':['Категория','МАСТЕР-КАТАЛОГ','Привязки и обязательные характеристики'],
'ce-attributes':['Характеристики','МАСТЕР-КАТАЛОГ','Типизированные параметры и единицы измерения'],
'ce-families':['Семейства','МАСТЕР-КАТАЛОГ','Фасовки, варианты и самостоятельные SKU'],
'ce-family':['Семейство','МАСТЕР-КАТАЛОГ','Список самостоятельных товаров'],
'ce-product':['Редактор товара','МАСТЕР-КАТАЛОГ','Данные, связи, комплектность и медиа'],
monitor:['Центр контроля','ОПЕРАЦИОННЫЙ КОНТРОЛЬ','Проблемные операции, очереди и готовность системы — без автоматических списаний'],
'receiving-readiness':['Готовность приёмки','ЛОГИСТИКА ПОСТАВОК','Проверка курьерских направлений, контактов склада и разрешений API'],
dashboard:['Обзор платформы','ОПЕРАЦИОННАЯ СВОДКА','Показатели, требующие внимания, и состояние системы.'],catalog:['Каталог товаров','КАТАЛОГ','Все товары общего каталога, публикация и поисковые запросы.'],offers:['Предложения продавцов','ЦЕНЫ И ПРЕДЛОЖЕНИЯ','Розничные предложения, их статусы и реальные остатки.'],inventory:['Складские остатки','УЧЁТ ОСТАТКОВ','Физическое количество, резерв и доступный остаток по складам.'],merchants:['Продавцы','MARKETPLACE','Организации, управляющие магазинами и предложениями.'],stores:['Магазины','MARKETPLACE','Витрины продавцов с отдельными настройками и товарами.'],used:['Б/у запчасти','УНИКАЛЬНЫЕ ТОВАРЫ','Отдельные экземпляры с привязкой к общему каталогу.'],nodes:['Склады и ПВЗ','ЛОГИСТИЧЕСКАЯ СЕТЬ','Адреса хранения и выдачи в разных городах.'],routes:['Маршруты доставки','ЛОГИСТИКА','Разрешённые перемещения между вашими складами и ПВЗ.'],suppliers:['Поставщики','ИНТЕГРАЦИИ','Работа API, лимиты, кэш и журнал запросов.'],accounts:['API-аккаунты','ИНТЕГРАЦИИ','Независимые договоры, учётные записи и лимиты поставщиков.'],destinations:['Адреса поставок','ИНТЕГРАЦИИ','Привязка аккаунтов поставщиков к точкам приёмки.'],orders:['Заказы покупателей','ОПЕРАЦИИ','Покупательские заказы и подробная информация по позициям.'],jobs:['Фоновые задания','ОПЕРАЦИИ','Очередь, попытки, ошибки и состояние выполнения.'],purchases:['Закупки поставщиков','ОПЕРАЦИИ','Подтверждённые и неопределённые результаты закупок; повторная отправка запрещена.'],refunds:['Возвраты','ОПЕРАЦИИ','Возвраты и финансовые статусы в тестовом контуре.']};
let token='',view='dashboard',page=1,search='',filters={},modalHandler=null,remember={};
async function api(path,options={}){
  if(!token)throw Error('Требуется административная авторизация');
  const resp=await fetch(path,{...options,cache:'no-store',headers:{'X-Admin-Token':token,...(options.body && !(options.body instanceof FormData)?{'Content-Type':'application/json'}:{})},credentials:'same-origin'});
  if(!resp.ok){let detail='HTTP '+resp.status;try{const j=await resp.json();const e=j.detail;if(typeof e==='string')detail=e;else if(Array.isArray(e))detail=e.map(v=>v.msg).join('; ');else if(e&&typeof e==='object'&&Array.isArray(e.issues))detail=e.issues.map(v=>v.message||v.code).join('; ')}catch{}if(resp.status===403){logout();throw Error('Доступ отклонён. Введите токен повторно.')}throw Error(detail)}
  if(resp.status===204)return null;return resp.json();
}
async function apiBlob(path){if(!token)throw Error('Требуется административная авторизация');const r=await fetch(path,{headers:{'X-Admin-Token':token},credentials:'same-origin',cache:'no-store'});if(!r.ok)throw Error('Файл недоступен: HTTP '+r.status);return r.blob()}
function toast(message,bad=false){const e=$('#alert');e.innerHTML=`<div class="message ${bad?'error':''}" role="status">${esc(message)}</div>`;setTimeout(()=>{if(e.textContent===message)e.innerHTML=''},6000)}
function badge(v){const val=String(v??'—');const good=['active','published','enabled','ready','success','confirmed','completed','paid','done'];const warn=['draft','review','pending','waiting','queued','processing','refreshing'];const danger=['error','failed','rejected','suspended','disabled','archived','cancelled'];const cls=good.includes(val)?'ok':warn.includes(val)?'warn':danger.includes(val)?'danger':'neutral';const trans={active:'Активен',published:'Опубликован',draft:'Черновик',review:'На проверке',paused:'Пауза',archived:'Архив',suspended:'Приостановлен',pending:'Ожидание',ready:'Актуально',error:'Ошибка',failed:'Ошибка',paid:'Оплачен',done:'Завершено',enabled:'Включено',disabled:'Отключено',completed:'Готово',processing:'В обработке',own_stock:'Свой склад',supplier:'Поставщик',warehouse:'Склад',pickup:'ПВЗ',warehouse_pickup:'Склад + ПВЗ',used_good:'Хорошее',used_fair:'Удовлетворительное',damaged:'С дефектами',for_parts:'На запчасти'};return `<span class="badge ${cls}">${esc(trans[val]||val)}</span>`}
function yes(v){return v?badge('active'):badge('disabled')}
function textCell(v){return esc(v??'—')}
function num(n){return new Intl.NumberFormat('ru-RU').format(Number(n)||0)}
function money(x){return new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB'}).format(Number(x||0))}
function surface(title,body,sub='',actions=''){return `<article class="surface"><div class="surface-head"><div><h2>${esc(title)}</h2>${sub?`<p>${esc(sub)}</p>`:''}</div>${actions}</div>${body}</article>`}
function empty(message='Здесь пока нет записей'){return `<div class="empty"><span class="empty-symbol">▤</span><b>${esc(message)}</b><span>Создайте первую запись или измените фильтры.</span></div>`}
function table(rows,cols,emptyMessage){if(!rows.length)return empty(emptyMessage);return `<div class="table-wrap"><table><thead><tr>${cols.map(c=>`<th>${esc(c[0])}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${c[1](r)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}
const t=(key)=>x=>textCell(x[key]), b=(key)=>x=>badge(x[key]), act=(label,action,id)=>`<button type="button" class="mini-action" data-action="${esc(action)}" data-id="${esc(id)}">${esc(label)}</button>`;
function headerAction(label,action){$('#heading-actions').innerHTML=label?`<button class="primary" data-action="${esc(action)}">＋ ${esc(label)}</button>`:''}
function render(title,html){$('#result').innerHTML=html;}
function setPage(next){view=next;page=1;search='';filters={};location.hash=next;$$('.nav-link').forEach(n=>n.classList.toggle('active',n.dataset.view===next));const parts=titles[next]||titles.dashboard;$('#crumb').textContent=parts[0];$('#page-title').textContent=parts[0];$('#eyebrow').textContent=parts[1];$('#page-subtitle').textContent=parts[2];$('#heading-actions').innerHTML='';$('#alert').innerHTML='';$('#sidebar').classList.remove('open');$('#mobile-dimmer').classList.remove('open');load()}
async function load(){const current=view;$('#result').innerHTML=empty('Загрузка данных…');try{const fn=pages[current]||dashboard;await fn()}catch(err){if(current===view)render('',surface('Не удалось загрузить данные',`<div class="surface-body"><p class="danger-text">${esc(err.message)}</p><button class="secondary-button" data-action="retry">Повторить</button></div>`))}}
function tile(label,count,icon,sub=''){return `<div class="tile"><div class="label">${esc(label)}</div><span class="corner">${icon}</span><b class="number">${num(count)}</b><div class="detail">${esc(sub)}</div></div>`}
const a2State={days:'30',bucket:'day',brand_id:'',category_id:'',merchant_id:'',supplier_code:'',article:'',preview_price:'1335'};
function a2BarChart(series,key,titleText,format){
 if(!series.some(r=>Number(r[key]||0)>0))return `<div class="a3-empty"><b>Заказов за выбранный период нет</b><span>График появится после оформления заказов с соответствующими данными. Тестовые финансовые операции здесь не учитываются.</span></div>`;
 const max=Math.max(1,...series.map(r=>Number(r[key]||0)));
 const step=Math.max(1,Math.ceil(series.length/9));
 return `<div class="a2-chart a3-chart" role="img" aria-label="${esc(titleText)}"><div class="a2-bars">${series.map((r,i)=>{
   const value=Number(r[key]||0),height=value?Math.max(3,value/max*100):0;
   return `<div class="a2-point" title="${esc(r.period+' — '+format(value))}" aria-label="${esc(r.period+' — '+format(value))}"><div class="a2-bar" style="height:${height.toFixed(2)}%"></div><small>${i%step===0?esc(r.period):''}</small></div>`;
 }).join('')}</div></div>`;
}
function a2OptionList(rows,selected,placeholder){return `<option value="">${esc(placeholder)}</option>`+rows.map(r=>`<option value="${esc(r.id)}" ${selected===r.id?'selected':''}>${esc(r.name)}</option>`).join('')}
async function analytics(){
 headerAction('','');
 const days=Number(a2State.days), globalParams=new URLSearchParams({days:String(days),bucket:a2State.bucket});
 const productParams=new URLSearchParams({days:String(days),limit:'20'});
 for(const key of ['brand_id','category_id','merchant_id','supplier_code'])if(a2State[key])productParams.set(key,a2State[key]);
 const supplierParams=new URLSearchParams({days:String(days)});
 if(a2State.supplier_code)supplierParams.set('supplier_code',a2State.supplier_code);
 const historyParams=new URLSearchParams({days:String(days),limit:'20'});
 if(a2State.supplier_code)historyParams.set('supplier_code',a2State.supplier_code);
 if(a2State.article)historyParams.set('article',a2State.article);
 const merchantParams=new URLSearchParams({days:String(days)});
 if(a2State.merchant_id)merchantParams.set('merchant_id',a2State.merchant_id);
 const [base,trend,products,suppliers,opts,customers,history,merchant,rounding]=await Promise.all([
   api(bo+'/analytics/overview?days='+Math.min(days,90)),api(bo+'/analytics/a2/trend?'+globalParams),
   api(bo+'/analytics/a2/products?'+productParams),api(bo+'/analytics/a2/suppliers?'+supplierParams),
   api(bo+'/analytics/a2/filters'),api(bo+'/analytics/a2/customers?days='+days),
   api(bo+'/analytics/a3/supplier-quotes?'+historyParams),api(bo+'/analytics/a3/merchants?'+merchantParams),
   api(bo+'/analytics/a3/rounding-preview?price='+encodeURIComponent(a2State.preview_price))]);
 const total=trend.series.reduce((acc,r)=>acc+r.orders_created,0);
 const face=trend.series.reduce((acc,r)=>acc+Number(r.final_order_value_rub||0),0);
 const filterForm=`<div class="filters a2-filterbar"><label>Период <select id="a2-days">${[['7','7 дней'],['30','30 дней'],['90','90 дней'],['365','365 дней']].map(([v,n])=>`<option value="${v}" ${a2State.days===v?'selected':''}>${n}</option>`).join('')}</select></label><label>Группировка <select id="a2-bucket">${[['day','Дни'],['week','Недели'],['month','Месяцы']].map(([v,n])=>`<option value="${v}" ${a2State.bucket===v?'selected':''}>${n}</option>`).join('')}</select></label><label>Бренд <select id="a2-brand">${a2OptionList(opts.brands,a2State.brand_id,'Все')}</select></label><label>Категория <select id="a2-category">${a2OptionList(opts.categories,a2State.category_id,'Все')}</select></label><label>Продавец <select id="a2-merchant">${a2OptionList(opts.merchants,a2State.merchant_id,'Все')}</select></label><label>Поставщик <select id="a2-supplier"><option value="">Все</option>${opts.suppliers.map(v=>`<option value="${esc(v)}" ${a2State.supplier_code===v?'selected':''}>${esc(v)}</option>`).join('')}</select></label><label>Артикул <input id="a3-article" value="${esc(a2State.article)}" placeholder="Например CF705" maxlength="90"></label><button class="primary" data-action="a2-apply">Применить</button></div>`;
 const warning=`<div class="notice warn"><b>!</b><div><b>Предварительная аналитика — не подтверждённая выручка.</b><br>Снимки поставщиков — исторические, не текущий остаток. Цены и сроки относятся к моменту записи. График заказов общий, фильтры по товарам применяются к рейтингу, продавец — к экономике продавцов, поставщик и артикул — к сравнению истории. Сумма заказа не равна оплате, спред не является прибылью. SHADOW F3–F5 исключён.</div></div>`;
 const stats=`<div class="tile-grid">${tile('Товаров в каталоге',base.catalog.products_total,'▤','Не продажи')}${tile('Поставщиков',base.suppliers.suppliers.length,'⬡','Исторические источники')}${tile('Сравнимых артикулов',history.rows.filter(x=>x.comparable).length,'⇄','В показанной выборке')}${tile('Заказов создано',total,'▧',days+' дней, без признака оплаты')}</div>`;
 const suppliersTable=table(suppliers.rows,[['Поставщик',t('supplier')],['Запросов в логах',r=>num(r.api_requests_logged)],['Средняя длительность, мс',r=>r.avg_logged_duration_ms===null?'—':esc(r.avg_logged_duration_ms)],['Снимков за период',r=>num(r.offer_snapshots_in_window)],['Исходы API',r=>esc(Object.entries(r.api_outcomes||{}).map(([k,v])=>k+': '+v).join(', ')||'—')]],'История API пока отсутствует');
 const historyRows=history.rows.flatMap(row=>row.suppliers.map(s=>({...s,brand:row.brand,article:row.article,product_name:row.name,paired:row.comparable})));
 const comparisonTable=table(historyRows,[['Товар',r=>`<b>${esc(r.brand)} ${esc(r.article)}</b><div class="sub">${esc(r.product_name)}</div>`],['Поставщик',r=>`<b>${esc(r.supplier)}</b>${r.paired?' <span class="badge ok">Сравним</span>':''}`],['Ист. мин./макс. закупка',r=>`${money(r.historical_min_purchase_rub)} — ${money(r.historical_max_purchase_rub)}`],['Сроки, дней',r=>r.min_lead_time_days===null?'—':esc(r.min_lead_time_days)+(r.max_lead_time_days!==r.min_lead_time_days?'–'+esc(r.max_lead_time_days):'')],['Снимков',r=>num(r.snapshots)],['Последнее наблюдение',r=>r.last_observed_at?esc(r.last_observed_at.slice(0,16).replace('T',' '))+' UTC':'—']],'Исторических сопоставленных предложений по выбранным условиям нет');
 const sellers=table(merchant.rows,[['Продавец',r=>`<b>${esc(r.merchant_name)}</b><div class="sub">${r.role==='OWNER'?'Собственный магазин':r.role==='AGENCY_MERCHANT'?'Партнёр':'Продавец не указан'}</div>`],['Заказов с финальной ценой',r=>num(r.orders_with_final_quotes)],['Стоимость строк, не выручка',r=>money(r.final_quote_line_value_rub)],['Спред своих товаров, не прибыль',r=>r.owner_buy_sell_spread_rub===null?'—':money(r.owner_buy_sell_spread_rub)],['Комиссия платформы',r=>r.platform_agency_fee_rub===null?'Не подтверждена':money(r.platform_agency_fee_rub)]],'Нет строк оформленных заказов с окончательной ценой');
 const ranks=table(products.rows,[['Товар',r=>`<b>${esc(r.brand)} ${esc(r.article)}</b><div class="sub">${esc(r.name)}</div>`],['Штук',r=>num(r.units)],['Стоимость строк, не выручка',r=>money(r.order_line_value_rub)],['Свой расчётный спред',r=>r.owner_spread_estimate_rub===null?'—':money(r.owner_spread_estimate_rub)]],'Оформленных заказов с окончательной ценой по выбранным фильтрам нет');
 const orderCharts=total?surface('Создание заказов',a2BarChart(trend.series,'orders_created','Создание заказов по UTC',n=>num(n)),'Общий показатель по площадке, независимо от товарных фильтров')+surface('Сумма оформленных заказов',a2BarChart(trend.series,'final_order_value_rub','Сумма финальных заказов по UTC',n=>money(n))+`<div class="surface-body"><b>${money(face)} за период</b><p class="sub">НЕ оплаченная выручка и НЕ прибыль.</p></div>`,'Не подтверждено банковской сверкой'):`<div class="a3-empty-compact"><b>Заказов за период нет</b><span>Поэтому не показываем два пустых графика. История поставщиков и каталог доступны выше.</span></div>`;
 const p=rounding; const roundBox=`<div class="surface-body"><div class="a3-round"><label>Расчётная цена, ₽ <input id="a3-price" type="number" min="0" max="1000000" step="0.01" value="${esc(a2State.preview_price)}"></label><button class="secondary-button" data-action="a2-round">Сравнить</button><div><small>Точные рубли</small><b>${money(p.exact_rub.customer_gross)}</b></div><div><small>Умное округление</small><b>${money(p.smart_up.customer_gross)}</b></div><div><small>Разница</small><b>${money(p.difference_rub)}</b></div></div><p class="sub">Только симуляция F2. Не меняет цены товаров. Конверсия покупателей не измерена.</p></div>`;
 const exports=`<div class="a2-exports"><button class="secondary-button" data-action="a2-export-supplier-history">↓ CSV история поставщиков</button><button class="secondary-button" data-action="a2-export-merchant-economics">↓ Excel продавцы</button><button class="secondary-button" data-action="a2-excel-products">↓ Excel товары</button><button class="secondary-button" data-action="a2-excel-trend">↓ Excel динамика</button><span class="sub">SHADOW исключён, без контактов клиентов</span></div>`;
 render('',filterForm+warning+stats+surface('Сравнение исторических цен поставщиков',comparisonTable,'Группировка по артикулу · даты и цены снимков · не является текущим наличием')+surface('Надёжность API по сохранённым логам',suppliersTable,'Наблюдавшиеся исходы запросов, а не подтверждённый SLA поставки')+surface('Экономика продавцов',sellers,'Собственная торговля и сторонние продавцы строго разделены')+surface('Влияние округления F2',roundBox,'Модельное сравнение без изменения реальных цен')+orderCharts+surface('Товары по сумме оформленных заказов',ranks,'Фильтры бренда, категории, продавца и поставщика; не оплаченные продажи')+surface('Экспорт A3',exports,'Исторические данные, не бухгалтерская отчётность')+surface('SHADOW — отдельно',`<div class="surface-body">F3: ${num(base.simulations.f3.entries)} тестовых проводок · F4: ${num(base.simulations.f4.simulated_payments)} тестовых платежей · F5: ${num(base.simulations.f5.tax_preview_rows)} строк налоговых симуляций.<p class="sub">Не включены в показатели выручки, прибыли и денежных расчётов.</p></div>`));
}
async function a2Action(name){
 if(name==='a2-apply'){
  for(const [k,el] of Object.entries({days:'a2-days',bucket:'a2-bucket',brand_id:'a2-brand',category_id:'a2-category',merchant_id:'a2-merchant',supplier_code:'a2-supplier'}))a2State[k]=$('#'+el)?.value||'';
  a2State.article=($('#a3-article')?.value||'').trim();if(a2State.article.length===1)throw Error('Для поиска по артикулу нужно минимум 2 символа');
  return load();
 }
 if(name==='a2-round'){const v=Number($('#a3-price')?.value||0);if(!Number.isFinite(v)||v<0||v>1000000)throw Error('Некорректная цена');a2State.preview_price=v.toFixed(2);return load()}
 if(name==='a2-export-supplier-history'||name==='a2-export-merchant-economics'){const params=new URLSearchParams({days:a2State.days});if(name==='a2-export-supplier-history'&&a2State.article)params.set('article',a2State.article);const supplier=name==='a2-export-supplier-history';const ext=supplier?'csv':'xlsx',suffix=supplier?'supplier-quotes':'merchants';const blob=await apiBlob(bo+'/analytics/a3/export/'+suffix+'.'+ext+'?'+params);const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='analytics_a3_'+suffix+'_'+a2State.days+'d.'+ext;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);return toast('Экспорт A3 подготовлен: '+suffix)}
 if(['a2-export-trend','a2-export-products','a2-excel-trend','a2-excel-products'].includes(name)){
  const params=new URLSearchParams({days:a2State.days});
  if(name.endsWith('-trend'))params.set('bucket',a2State.bucket);
  else for(const key of ['brand_id','category_id','merchant_id','supplier_code'])if(a2State[key])params.set(key,a2State[key]);
  const suffix=name.endsWith('-trend')?'trend':'products';const ext=name.startsWith('a2-excel-')?'xlsx':'csv';
  const file=await apiBlob(bo+'/analytics/a2/export/'+suffix+'.'+ext+'?'+params);
  const url=URL.createObjectURL(file),link=document.createElement('a');
  link.href=url;link.download='analytics_a2_'+suffix+'_'+a2State.days+'d.'+ext;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
  return toast(ext.toUpperCase()+' экспортирован: предварительные операционные данные');
 }
}
async function dashboard(){
 const [s,ops,orders,attention,config]=await Promise.all([api(wb+'/summary'),api(op+'/overview'),api(op+'/orders'),api(mon+'/attention'),api(mon+'/configuration')]);
 const tiles=`<div class="tile-grid">${tile('Товаров в каталоге',s.products,'▤',s.review_products+' ожидают публикации')}${tile('Активных предложений',s.active_offers,'▥',s.offers+' всего предложений')}${tile('Заказов на проверке',attention.review_orders,'◷','Открыть центр контроля')}${tile('Ошибок фоновых заданий',attention.failed_jobs,'!','Не повторять внешние операции вручную')}</div>`;
 const configHtml=`<div class="status-box"><div><strong>API поставщиков</strong><small>Разрешение чтения и поиска</small></div>${yes(config.supplier_api_enabled)}</div><div class="status-box"><div><strong>Закупки через поставщиков</strong><small>Глобальный переключатель и разрешение боевых отправок</small></div>${badge(config.supplier_procurement_enabled&&config.real_supplier_checkout_enabled?'enabled':'disabled')}</div><div class="status-box"><div><strong>СБП — реквизиты</strong><small>Настройка доступа не означает проверку реальных платежей</small></div>${yes(config.sbp_credentials_configured)}</div>`;
 const quick=`<div class="quick-grid"><button class="quick-card" data-view="monitor"><span class="quick-icon">◷</span><b>Центр контроля →</b><small>Проверки и исключения</small></button><button class="quick-card" data-view="receiving-readiness"><span class="quick-icon">⌂</span><b>Готовность приёмки →</b><small>Курьерские адреса и контакты</small></button><button class="quick-card" data-view="catalog"><span class="quick-icon">▤</span><b>Каталог товаров →</b><small>Ручная публикация</small></button></div>`;
 const recents=table(orders.slice(0,6),[['Заказ',r=>`<b>${esc(r.id.slice(0,8))}…</b>`],['Сумма',r=>money(r.total)],['Статус',b('status')],['Действие',r=>act('Открыть','order',r.id)]],'Заказов пока нет');
 const notice=`<div class="notice"><b>ⓘ</b><div><b>Фактические настройки среды: ${esc(config.environment)}.</b><br>Аккаунт, доступный API и переключатели не подтверждают возможность реальной оплаты или закупки. Отправка поставщику здесь не выполняется.</div></div>`;
 render('',tiles+notice+surface('Быстрый доступ',`<div class="surface-body">${quick}</div>`)+`<div class="split">${surface('Последние заказы',recents,'До 6 последних заказов')}${surface('Настройки исполнения',configHtml,'Данные из активной конфигурации')}</div>`)
}
const monitoringReasons={account_disabled:'Аккаунт отключён',search_disabled:'Поиск через API отключён',destination_disabled:'Направление выключено',destination_unverified:'Адрес не подтверждён',node_inactive:'Точка не активна',merchant_mismatch:'Не совпадает продавец точки и аккаунта',receiving_contact_invalid:'ФИО или телефон точки отсутствуют либо не проходят проверку',supplier_address_missing:'Нет адреса поставщика',supplier_delivery_missing:'Не выбран способ доставки',rossko_courier_required:'ROSSKO: требуется курьер 000000002'};
async function monitor(){
 headerAction('','');
 const [stats,readiness,config]=await Promise.all([api(mon+'/attention'),api(mon+'/receiving-readiness'),api(mon+'/configuration')]);
 const tiles=`<div class="tile-grid">${tile('Заказов на проверке',stats.review_orders,'▧')}${tile('Закупок на сверке',stats.review_purchases,'↗')}${tile('Возвратов на проверке',stats.review_refunds,'↶')}${tile('Сбоев заданий',stats.failed_jobs,'!')}</div>`;
 const btns=`<div class="quick-grid"><button class="quick-card" data-view="orders"><b>Все заказы →</b><small>Фильтр по статусу и история</small></button><button class="quick-card" data-view="purchases"><b>Закупки →</b><small>Не повторять неподтверждённые заявки</small></button><button class="quick-card" data-view="receiving-readiness"><b>Адреса поставки →</b><small>${num(readiness.action_required)} требуют настройки</small></button></div>`;
 const group=(source,title)=>surface(title,table(Object.entries(source).map(([status,count])=>({status,count})).sort((a,b)=>b.count-a.count),[['Статус',r=>badge(r.status)],['Количество',r=>num(r.count)]],'Нет операций'),'Исторические агрегаты без автоматического изменения статусов');
 const note=`<div class="notice"><b>⚑</b><div>Очередь: ${num(stats.overdue_queued_jobs)} просроченных заданий. Доступ API: ${config.supplier_api_enabled?'включён':'выключен'}, боевые закупки: ${config.supplier_procurement_enabled&&config.real_supplier_checkout_enabled?'переключатели включены, но внешние разрешения требуют проверки':'отключены'}. Ни одна кнопка этой панели не отправляет заказ и не списывает деньги.</div></div>`;
 render('',tiles+note+surface('Перейти к проблеме',`<div class="surface-body">${btns}</div>`)+`<div class="split">${group(stats.orders,'Состояния заказов')}${group(stats.jobs,'Состояния фоновых задач')}</div>`+`<div class="split">${group(stats.supplier_purchase_attempts,'Попытки закупок ROSSKO')}${group(stats.refunds,'Возвраты СБП')}</div>`);
}
async function receivingReadiness(){
 headerAction('','');const data=await api(mon+'/receiving-readiness');
 const box=`<div class="notice"><b>Курьерская доставка</b><div>Проверка исключительно сохранённой конфигурации БД; живой вызов поставщика здесь не выполняется. Данные получателя не выводятся в списке, используются только признаки заполненности.</div></div>`;
 const rows=table(data.items,[['Поставщик / аккаунт',r=>`<b>${esc(r.supplier.toUpperCase())}</b><div class="sub">${esc(r.account)}</div>`],['Склад / ПВЗ',r=>`<b>${esc(r.node)}</b><div class="sub">${esc(r.city)}</div>`],['Курьер',r=>esc(r.courier_delivery_id||'Не указан')],['Контакт точки',r=>yes(r.contact_configured)],['Настройка',r=>r.issues.length?`<span class="danger-text">${r.issues.map(x=>esc(monitoringReasons[x]||x)).join('; ')}</span>`:badge('ready')],['Действия',r=>act('Склад','monitor-node',r.node_id)+' '+act('Адрес','monitor-dest',r.destination_id)]],'Адреса поставок ещё не созданы');
 const missing=table(data.unmapped_accounts||[],[['Поставщик',r=>esc(r.supplier.toUpperCase())],['Аккаунт',t('label')],['Действие',r=>act('Привязать адрес','monitor-dest',r.id)]],'Все зарегистрированные API-аккаунты имеют хотя бы одно направление');
 render('',box+`<div class="tile-grid">${tile('Направлений показано',data.total_shown,'⌾')}${tile('Без замечаний',data.ready,'✓')}${tile('Требуют настройки',data.action_required,'!')}${tile('Без адреса поставки',(data.unmapped_accounts||[]).length,'◈')}</div>`+surface('Готовность направлений',rows,'Не подтверждает доступность товаров или цену курьера'+(data.truncated?'; список ограничен 1000 строками':''))+surface('API-аккаунты без адреса приёмки',missing,'Настройте связь с конкретным складом или ПВЗ'+(data.unmapped_truncated?'; список ограничен 500 аккаунтами':'')));
}
async function orders(){
 headerAction('','');const args=new URLSearchParams({page:String(page),size:'30'});if(filters.orderReview)args.set('review_only','true');else if(filters.orderStatus)args.set('status',filters.orderStatus);
 const data=await api(mon+'/orders?'+args);const filter=`<div class="filters"><label>Статус <input id="orders-status" maxlength="40" placeholder="Все статусы" value="${esc(filters.orderStatus||'')}"></label>${filters.orderReview?'<span class="badge warn">Требуют проверки</span>':''}<button class="secondary-button" data-action="orders-filter">Применить</button><button class="secondary-button" data-action="orders-review">На проверке</button><button class="secondary-button" data-action="orders-all">Сбросить</button></div>`;
 const rows=table(data.items,[['Заказ',r=>`<b>${esc(r.id.slice(0,13))}…</b><div class="sub">${esc(r.created_at.slice(0,16).replace('T',' '))}</div>`],['Сумма',r=>r.total_is_final?money(r.total):`<span class="sub">Предварительно ${money(r.total)}</span>`],['Статус',b('status')],['Действие',r=>act('История и позиции','order',r.id)]],'Заказов с выбранным статусом нет');
 const pages=`<div class="pagination"><span>Всего ${num(data.total)} · Страница ${page}</span><button class="mini-action" data-action="prev" ${page===1?'disabled':''}>←</button><button class="mini-action" data-action="next" ${page*30>=data.total?'disabled':''}>→</button></div>`;
 render('',surface('Заказы покупателей',filter+rows+pages,'Постраничная выборка, без вывода контактных данных покупателей в списке'));
}
async function catalog(){headerAction('Добавить товар','new-product');const u=new URLSearchParams({page:String(page),size:'30'});if(search)u.set('q',search);if(filters.status)u.set('status',filters.status);const [data,brands]=await Promise.all([api(wb+'/catalog?'+u),api(wb+'/brands')]);remember.brands=brands;const filter=`<div class="filters"><input id="q" placeholder="Артикул, бренд или название" value="${esc(search)}" class="flex-grow"><button class="secondary-button" data-action="search">Найти</button><select id="status-filter"><option value="">Все статусы</option>${['draft','review','published','archived'].map(k=>`<option value="${k}" ${filters.status===k?'selected':''}>${esc(k)}</option>`).join('')}</select><button class="secondary-button" data-action="new-brand">＋ Бренд</button></div>`;
 const rows=table(data.items,[['Товар',r=>`<span class="strong">${esc(r.name)}</span><div class="sub">${esc(r.brand)} · ${esc(r.article)}</div>`],['Артикул',t('article')],['Статус',b('status')],['URL',r=>`<span class="sub">${esc(r.slug)}</span>`],['Действия',r=>act('Редактор','ce-product',r.id)+' '+(r.status==='draft'||r.status==='review'?act('Опубликовать','publish',r.id):`<a href="/product/${encodeURIComponent(r.slug)}" target="_blank" rel="noopener" class="mini-action">Просмотр ↗</a>`)]],'Товары не найдены');const footer=`<div class="pagination"><span>Всего ${num(data.total)} · Страница ${page}</span><button class="mini-action" data-action="prev" ${page===1?'disabled':''}>←</button><button class="mini-action" data-action="next" ${page*30>=data.total?'disabled':''}>→</button></div>`;render('',surface('Товары',filter+rows+footer,'Добавление, проверка и публикация'))}
async function generic(name,endpoint,columns,createAction=null,opts={}){if(createAction)headerAction(opts.actionText||'Добавить',createAction);const data=await api(endpoint);remember[name]=data;let rows=data;if(!Array.isArray(rows))rows=data.items||[];
 const controls=opts.filter?`<div class="filters"><input id="local-filter" placeholder="Поиск по списку" value="${esc(search)}" class="flex-grow"><button data-action="local-search" class="secondary-button">Найти</button></div>`:'';
 if(search&&opts.filter)rows=rows.filter(r=>JSON.stringify(r).toLowerCase().includes(search.toLowerCase()));
 render('',surface(opts.tableTitle||titles[name][0],controls+table(rows,columns,opts.empty),'Показаны последние '+rows.length+' записей'))}
async function merchants(){await generic('merchants',wb+'/merchants',[['Продавец',r=>`<b class="strong">${esc(r.name)}</b><div class="sub">${esc(r.slug)}</div>`],['Роль',r=>r.platform_owner?badge('active'):'Сторонний'],['Статус',b('status')],['ID',r=>`<span class="sub">${esc(r.id.slice(0,8))}</span>`]],'new-merchant')}
async function stores(){await generic('stores',wb+'/stores',[['Магазин',r=>`<b class="strong">${esc(r.name)}</b><div class="sub">${esc(r.slug)}</div>`],['Продавец',t('merchant')],['Статус',b('status')]],'new-store')}
async function nodes(){await generic('nodes',wb+'/nodes',[['Точка',r=>`<b class="strong">${esc(r.name)}</b><div class="sub">${esc(r.code)}</div>`],['Тип',b('kind')],['Город',t('city')],['Адрес',t('address')],['Продавец',t('merchant')],['Состояние',r=>yes(r.active)],['Получатель поставок',r=>yes(r.receiving_contact_configured)],['Действие',r=>act('Редактировать','edit-node',r.id)]],'new-node',{filter:true})}
async function routes(){await generic('routes',wb+'/routes',[['Откуда',t('source')],['Куда',t('target')],['Доставка',r=>r.transit_days+' дн.'],['Обработка',r=>r.handling_days+' дн.'],['Стоимость',r=>money(r.fee)],['Активен',r=>yes(r.active)],['Действие',r=>act('Редактировать','edit-route',r.id)]],'new-route')}
async function offers(){headerAction('Создать предложение','new-offer');const u=search?'?q='+encodeURIComponent(search):'';const data=await api(wb+'/offers'+u);remember.offers=data;const box='<div class="filters"><input id="q" class="flex-grow" placeholder="Поиск по SKU" value="'+esc(search)+'"><button class="secondary-button" data-action="search">Найти</button></div>';render('',surface('Предложения магазинов',box+table(data,[['SKU / Магазин',r=>`<b class="strong">${esc(r.sku)}</b><div class="sub">${esc(r.store)} · ${esc(r.merchant)}</div>`],['Тип',b('source_kind')],['Цена',r=>money(r.price)],['Остаток',r=>`<b>${num(r.on_hand-r.reserved)}</b><div class="sub">Резерв ${num(r.reserved)}</div>`],['Статус',b('status')],['Действия',r=>act('Изменить','edit-offer',r.id)+' '+(r.source_kind==='own_stock'?act('Остаток','edit-stock',r.id):'')]],'Нет предложений'),'Все изменения проходят проверки каталога и владельца склада'))}
async function inventory(){await generic('inventory',wb+'/inventory',[['SKU',r=>`<b class="strong">${esc(r.sku)}</b>`],['Точка',r=>`<b>${esc(r.node)}</b><div class="sub">${esc(r.city)}</div>`],['Всего',t('on_hand')],['Резерв',t('reserved')],['Доступно',r=>`<b class="strong">${num(r.available)}</b>`],['Действие',r=>act('Изменить','edit-stock',r.offer_id+'|'+r.node_id)]],null,{filter:true})}
async function used(){await generic('used',wb+'/used',[['Товар',r=>`<b class="strong">${esc(r.title)}</b><div class="sub">${esc(r.merchant)}</div>`],['Состояние',b('condition')],['Пробег, км',r=>textCell(r.mileage_km)],['Фото',t('photo_count')],['Статус',b('status')],['Действие',r=>act('Статус','edit-used',r.id)]],'new-used')}
async function accounts(){await generic('accounts',wb+'/accounts',[['Подключение',r=>`<b class="strong">${esc(r.label)}</b><div class="sub">${esc(r.supplier)}</div>`],['Продавец',t('merchant')],['Профиль',t('credential_profile')],['Лимит/час',t('requests_per_hour')],['Поиск',r=>yes(r.read_enabled)],['Закупка',r=>yes(r.procurement_enabled)],['Состояние',r=>yes(r.enabled)],['Действия',r=>act('Настроить','edit-account',r.id)+' '+(r.supplier_code==='rossko'?act('Адреса API','probe-account',r.id):'')]],'new-account')}
async function destinations(){await generic('destinations',wb+'/destinations',[['Аккаунт',t('account')],['Точка приёмки',r=>`<b class="strong">${esc(r.node)}</b><div class="sub">${esc(r.city)}</div>`],['Внешний адрес',t('external_address_id')],['Доставка',t('external_delivery_id')],['Реквизит',r=>r.external_requisite_id?esc(r.external_requisite_id):'<span class="sub">Авто через API</span>'],['Проверено',r=>yes(r.verified)],['Включено',r=>yes(r.enabled)],['Действие',r=>act('Редактировать','edit-destination',r.id)]],'new-destination')}
async function jobs(){
 headerAction('','');const args=new URLSearchParams({page:String(page),size:'30'});if(filters.jobStatus)args.set('status',filters.jobStatus);
 const data=await api(mon+'/jobs?'+args);
 const controls=`<div class="filters"><label>Статус <input id="jobs-status" placeholder="Все статусы" maxlength="30" value="${esc(filters.jobStatus||'')}"></label><button data-action="jobs-filter" class="secondary-button">Применить</button><button data-action="jobs-all" class="secondary-button">Сбросить</button></div>`;
 const rows=table(data.items,[['Тип',t('kind')],['Статус',b('status')],['Попытки',r=>num(r.attempts)],['Срок',r=>esc(r.due_at.slice(0,19).replace('T',' '))],['Диагностика',r=>r.error_present?'Ошибка зарегистрирована':'—']],'Заданий пока нет');
 const foot=`<div class="pagination"><span>Всего ${num(data.total)} · Страница ${page}</span><button class="mini-action" data-action="prev" ${page===1?'disabled':''}>←</button><button class="mini-action" data-action="next" ${page*30>=data.total?'disabled':''}>→</button></div>`;
 render('',surface('Очередь заданий',controls+rows+foot,'Ошибки не выводятся сырым текстом — в них могут быть внешние секреты. Без ручных повторов.'));
}
async function purchases(){
 headerAction('','');const args=new URLSearchParams({page:String(page),size:'30'});if(filters.purchaseStatus)args.set('status',filters.purchaseStatus);
 const data=await api(mon+'/purchases?'+args);
 const controls=`<div class="filters"><label>Статус <input id="purchase-status" placeholder="Все статусы" maxlength="32" value="${esc(filters.purchaseStatus||'')}"></label><button data-action="purchases-filter" class="secondary-button">Применить</button><button data-action="purchases-all" class="secondary-button">Сбросить</button></div>`;
 const rows=table(data.items,[['Попытка',r=>`<span class="sub">${esc(r.id.slice(0,12))}</span>`],['Заказ покупателя',r=>r.order_id?act('Открыть заказ','order',r.order_id):'Не привязан'],['Статус',b('status')],['Заказы поставщика',r=>esc((r.remote_order_ids||[]).join(', ')||'—')],['Ошибка',r=>esc(r.error_code||'—')],['Направление',r=>esc(r.destination_id.slice(0,8))]],'Заявок на закупку нет');
 const foot=`<div class="pagination"><span>Всего ${num(data.total)} · Страница ${page}</span><button class="mini-action" data-action="prev" ${page===1?'disabled':''}>←</button><button class="mini-action" data-action="next" ${page*30>=data.total?'disabled':''}>→</button></div>`;
 render('',`<div class="notice"><b>Безопасность</b><div>Журнал только для чтения. При неизвестном результате предыдущей закупки нельзя отправлять новую без независимой сверки поставщика. Тестовые вызовы ROSSKO могут не сохраняться как коммерческие закупки.</div></div>`+surface('Журнал закупок',controls+rows+foot,'С привязкой к заказам покупателей и статусам внешних заявок'));
}
async function refunds(){await generic('refunds',op+'/refunds',[['Возврат',r=>`<b class="strong">${esc(r.id.slice(0,12))}</b>`],['Платёж',r=>`<span class="sub">${esc(r.payment_id.slice(0,12))}</span>`],['Сумма',r=>money(r.amount)],['Статус',b('status')]])}
async function suppliers(){headerAction('Добавить поставщика','new-supplier');const d=await api(en+'/suppliers');remember.supplierStatus=d;const cards=d.map(s=>`<article class="supplier-card"><div style="display:flex;justify-content:space-between;gap:10px"><div><h3>${esc(s.code.toUpperCase())}</h3><p>Источник закупочных предложений</p></div>${badge(s.live_search_possible?'ready':'disabled')}</div><div class="supplier-figures"><div><b>${num(Object.values(s.cache_statuses||{}).reduce((a,v)=>a+v,0))}</b><small>Кэш</small></div><div><b>${num(s.requests_24h?.success)}</b><small>Успешных</small></div><div><b>${num(s.requests_24h?.error)}</b><small>Ошибок</small></div></div><p>Учётные данные: ${s.credentials_configured?'настроены':'не настроены'}</p><button class="secondary-button" data-action="supplier" data-id="${esc(s.code)}">Управление →</button></article>`).join('');render('',`<div class="notice"><b>ⓘ</b><div>Поставщик и его API-аккаунты — разные сущности. Лимиты и адреса доставки настраиваются в отдельных разделах. Запросы к API выполняются только при явном включении.</div></div><div class="supplier-grid">${cards||empty('Поставщики не настроены')}</div>`)}
async function supplierDetails(code){view='suppliers';$('#heading-actions').innerHTML='';$('#page-title').textContent='Поставщик '+code.toUpperCase();$('#page-subtitle').textContent='Политика запросов, кэш и журнал ошибок';$('#result').innerHTML=empty('Загрузка данных…');try{
 const [cfg,logs,cache,offers]=await Promise.all([api(en+'/suppliers/'+encodeURIComponent(code)+'/configuration'),api(en+'/suppliers/'+encodeURIComponent(code)+'/logs?limit=30'),api(en+'/suppliers/'+encodeURIComponent(code)+'/cache?limit=30'),api(op+'/suppliers/'+encodeURIComponent(code)+'/offers')]);
 const fields=[['enabled','Разрешить поиск','checkbox'],['positive_ttl_seconds','TTL предложения, сек','number'],['empty_ttl_seconds','TTL пустого результата, сек','number'],['timeout_seconds','Тайм-аут, сек','number'],['min_interval_seconds','Пауза, сек','number']];
 const form=`<div class="supplier-setting">${fields.map(([id,label,type])=>`<label>${esc(label)}<input data-setting="${id}" type="${type}" ${type==='checkbox'?(cfg[id]?'checked':''):`value="${esc(cfg[id])}"`}></label>`).join('')}</div><p style="margin-top:18px"><button class="primary" data-action="save-supplier" data-id="${esc(code)}">Сохранить настройки</button> <button class="secondary-button" data-action="supplier-refresh" data-id="${esc(code)}">Обновить товар по UUID</button></p>`;
 const logsTable=table(logs,[['Дата',t('time')],['Метод',t('action')],['Результат',b('outcome')],['Время, мс',t('duration_ms')],['Ошибка',t('error_type')]]);
 const cacheTable=table(cache,[['Артикул / ключ',t('lookup_key')],['Статус',b('status')],['Найдено',t('result_count')],['Задержка',t('last_duration_ms')]]);
 const offersTable=table(offers.slice(0,40),[['Артикул',t('article')],['Бренд',t('brand')],['Сопоставление',b('matching')],['Закупка',r=>r.current_offer?money(r.current_offer.purchase):'—'],['Количество',r=>r.current_offer?textCell(r.current_offer.quantity):'—']]);
 render('',`<p><button class="secondary-button" data-action="back-suppliers">← Назад к поставщикам</button></p>`+surface('Политика API',`<div class="surface-body">${form}</div>`)+surface('Последние запросы',logsTable)+surface('Состояние кэша',cacheTable)+surface('Закупочные предложения',offersTable))
 }catch(e){render('',surface('Ошибка',`<div class="surface-body">${esc(e.message)}</div>`))}}

async function finance(){
 const data=await api(bo+'/finance/overview');
 remember.finance=data;
 headerAction('Добавить юрлицо','fin-new-entity');
 const intro='<div class="notice"><b>ⓘ</b><div><b>Finance F1 — налоговый фундамент.</b><br>Режим владельца не выбран; все новые профили — черновики. Расчёт НДС только демонстрационный, ни одна операция оплаты или заказа не изменяется.</div></div>';
 const entities=table(data.entities,[['Юрлицо',t('name')],['Тип',t('entity_type')],['ИНН',t('inn')],['Проверка',r=>badge(r.verification_status)],['Настройка',r=>act('Создать налоговый профиль','fin-new-profile',r.id)]],'Юридических лиц пока нет');
 const profiles=table(data.profiles,[['Начало',t('valid_from')],['Режим',t('regime')],['НДС',t('vat_scheme')],['Статус',r=>badge(r.status)],['Основание',t('evidence_reference')]],'Налоговые профили ещё не созданы');
 const controls='<div class="surface-body"><button class="secondary-button" data-action="fin-preview">Рассчитать НДС (симуляция)</button> <button class="secondary-button" data-action="fin-rules">Налоговые пороги 2026</button></div>';
 render('',intro+surface('Юридические лица',entities,'Привязка к существующим продавцам')+surface('Налоговые профили',profiles,'Не активируем режим без подтверждённых документов')+surface('Налоговый расчёт',controls,'Только предварительный расчёт, без записи в БД'));
}
async function financePricing(){
 const d=await api(bo+'/finance/pricing/overview');remember.f2=d;headerAction('Правило наценки','f2-new-rule');
 const notice='<div class="notice"><b>ⓘ</b><div><b>F2 — моделирование финансов.</b><br>Ни одно правило F2 пока не влияет на реальные цены каталога, предложения поставщиков, СБП и заказы. Реальный налоговый режим владельца не выбран.</div></div>';
 const controls='<div class="quick-grid"><button class="quick-card" data-action="f2-preview-own"><b>Рассчитать цену товара</b><small>Закупка, наценка, логистика, НДС</small></button><button class="quick-card" data-action="f2-preview-agent"><b>Рассчитать продажу партнёра</b><small>Комиссия платформы, выплата, СБП</small></button><button class="quick-card" data-action="f2-new-fee"><b>Добавить тариф комиссии</b><small>Настройки в черновике</small></button></div>';
 const rules=table(d.pricing_rules,[['Название',t('name')],['Область',t('scope_kind')],['Наценка',r=>esc(r.markup_percent)+'%'],['Цена',r=>esc(r.rounding_mode==='SMART_UP'?'Умное ↑':'Точные ₽')],['Мин. цена',r=>money(r.floor_price)],['Дата',t('valid_from')],['Статус',r=>badge(r.state)],['Действие',r=>r.state!=='retired'?act('Архивировать','f2-retire-rule',r.id):'—']],'Нет сохранённых правил');
 const fees=table(d.fee_schedules,[['Тариф',t('name')],['Тип',t('fee_kind')],['Ставка',r=>esc(r.percent)+'% + '+money(r.fixed_amount)],['Лимит',r=>r.cap_amount?money(r.cap_amount):'—'],['Основание',t('evidence_reference')],['Статус',r=>badge(r.state)],['Действие',r=>r.state!=='retired'?act('Архивировать','f2-retire-fee',r.id):'—']],'Нет тарифов');
 let quote='';const q=remember.f2Quote;
 if(q){let entries=[['Сценарий',q.sale_model==='OWN'?'Собственная продажа':'Агентская продажа'],['Цена покупателю',money(q.customer_gross)],['До округления',money(q.unrounded_customer_gross)],['Округление',esc(q.rounding_mode)+(q.rounding_step_used?' · шаг '+esc(q.rounding_step_used)+' ₽':'' )],['Прибавка от округления',money(q.rounding_uplift)],['Ограничения цены',q.price_ceiling_met===false||q.rounding_uplift_cap_met===false?'НАРУШЕНЫ — цену не применять':'Соблюдены'],['Закупка',q.purchase_gross?money(q.purchase_gross):'—'],['Комиссия платформы',money(q.platform_fee_gross)],['СБП',money(q.bank_fee)],['Выплата продавцу',q.seller_payout_gross?money(q.seller_payout_gross):'—'],['Денежный остаток до налогов',money(q.cash_contribution_before_tax)],['Оценка НДС реализации / комиссии',q.output_vat_estimate===null?'Не определено':money(q.output_vat_estimate)],['Остаток после оценочного НДС',q.net_contribution_estimate===null?'Не определено':money(q.net_contribution_estimate)]];
 quote=surface(q.status==='SIMULATION_CONSTRAINT_FAILED'?'Расчёт ЗАБЛОКИРОВАН — лимиты превышены':'Последняя симуляция',`<div class="surface-body">${table(entries.map(([name,value])=>({name,value})),[['Показатель',t('name')],['Сумма',t('value')]])}<p class="sub">${esc(q.warnings.join(' · '))}</p></div>`);}
 render('',notice+surface('Симуляции',`<div class="surface-body">${controls}</div>`)+quote+surface('Правила наценок',rules)+surface('Тарифы и комиссии',fees));
}
async function financePayments(){
 const root=bo+'/finance/payments-sim',d=await api(root+'/overview'),r=await api(root+'/reconciliation');remember.f4=d;remember.f4Bank=r;
 headerAction('Тестовый СБП-платёж','f4-create-payment');
 const notice='<div class="notice"><b>ⓘ</b><div><b>Finance F4 — только SHADOW / MOCK_SBP.</b><br>Никаких реальных операций банка, выдачи чеков, возвратов, закупок и проводок. Статусы подтверждаются исключительно симулятором.</div></div>';
 const s=d.counts;
 const summary='<div class="tile-grid">'+tile('Тестовых платежей',s.payments,'₽')+tile('Тестовых возвратов',s.refunds,'↶')+tile('Планов чеков',s.receipt_plans,'▧')+tile('Сверенных строк',s.matches,'⇄')+'</div>';
 const payments=table(d.payments,[['ID заказа',r=>'<b>'+esc(r.sim_order_ref)+'</b>'],['Сумма',r=>money(r.amount)],['Состояние',r=>badge(r.status)],['Вид',t('sale_model')],['Действия',r=>act('Событие','f4-event',r.id)+' '+act('Возврат','f4-refund',r.id)+' '+act('Чек','f4-receipt',r.id)+' '+act('Детали','f4-detail',r.id)]],'Тестовых платежей пока нет');
 const bank=table(r.lines,[['Тестовая строка',t('external_ref')],['Направление',t('direction')],['Сумма',r=>money(r.amount)],['Сверка',t('status')],['Действия',r=>r.status==='MATCHED'?'Сверено':act('Сопоставить','f4-match',r.id)]],'Симуляций банковских выписок нет');
 render('',notice+summary+surface('СБП — тестовые платежи',payments,'Подтверждение только вручную через mock-событие')+surface('Условная банковская сверка',bank,'Реквизиты и банковские выписки не загружаются',act('Добавить строку','f4-bank','')));
}
async function financePaymentsAction(name,id){
 const root=bo+'/finance/payments-sim';
 const stamp=()=>Date.now().toString();
 if(name==='f4-create-payment')return modal('Создать ТЕСТОВЫЙ СБП-платёж','Это не запрос в банк и не оплата заказа.',[
    fld('sim_order_ref','Тестовый заказ SIM:ORDER:...','text',true,{value:'SIM:ORDER:'+stamp()}),
    fld('idempotency_key','Уникальный ключ','text',true,{value:'SIM:PAY:'+stamp()}),
    fld('amount','Сумма, ₽','number',true,{value:'2000'}),
    fld('sale_model','Модель продажи','select',true,{options:[['OWN','Собственная продажа'],['AGENCY','Агентская продажа']]})
 ],b=>api(root+'/payments',{method:'POST',body:JSON.stringify(b)}));
 if(name==='f4-event'){
   const p=remember.f4?.payments.find(x=>x.id===id);if(!p)throw Error('Тестовый платёж не найден');
   return modal('Событие MOCK_SBP','Статус вводит администратор; никакого взаимодействия с платёжным провайдером.',[
     fld('event_key','Уникальный ключ события','text',true,{value:'SIM:EVENT:'+stamp()}),
     fld('new_status','Статус','select',true,{options:[['PENDING','В обработке'],['SUCCEEDED','Условно оплачен'],['CANCELED','Условно отменён']]}),
     fld('amount','Проверяемая сумма, ₽','number',true,{value:p.amount})
   ],b=>api(root+'/payments/'+id+'/mock-events',{method:'POST',body:JSON.stringify({...b,method:'MOCK_SBP',currency:'RUB'})}));
 }
 if(name==='f4-refund')return modal('Запросить ТЕСТОВЫЙ возврат','Возврат останется условным; реальный банк не вызывается.',[
   fld('request_key','Уникальный ключ','text',true,{value:'SIM:REF:'+stamp()}),fld('amount','Сумма возврата, ₽','number',true,{value:'100'})
 ],b=>api(root+'/payments/'+id+'/refunds',{method:'POST',body:JSON.stringify(b)}));
 if(name==='f4-refund-result'){if($('#modal').open)$('#modal').close();return modal('Результат ТЕСТОВОГО возврата','Финальный результат задаётся только симулятором.',[
   fld('status','Результат','select',true,{options:[['SUCCEEDED','Условно завершён'],['REJECTED','Отклонён']]})
 ],b=>api(root+'/refunds/'+id+'/mock-result',{method:'POST',body:JSON.stringify(b)}));}
 if(name==='f4-receipt')return modal('План чека — НЕ ФИСКАЛИЗАЦИЯ','План сохраняется только для моделирования; чек в ОФД не отправляется.',[
   fld('request_key','Ключ плана','text',true,{value:'SIM:RCPT:'+stamp()}),
   fld('kind','Тип расчёта','select',true,{options:[['PREPAYMENT','Приём предоплаты'],['OFFSET','Зачёт предоплаты'],['REFUND','Возврат прихода']]}),
   fld('amount','Сумма, ₽','number',true,{value:'2000'}),
   fld('vat_note','Налоговое основание (тест)','select',true,{options:[['UNCONFIRMED','Налоговый режим не подтверждён'],['MOCK_OSNO_22','Тестовый НДС 22%'],['MOCK_USN_NO_VAT','Тестовая УСН без НДС']]})
 ],b=>api(root+'/payments/'+id+'/receipt-plans',{method:'POST',body:JSON.stringify(b)}));
 if(name==='f4-detail'){
  const d=await api(root+'/payments/'+id),ref=table(d.refunds,[['Возврат',r=>esc(r.id)],['Сумма',r=>money(r.amount)],['Статус',t('status')],['Действия',r=>r.status==='REQUESTED'?act('Результат','f4-refund-result',r.id):'—']],'Возвратов нет'),rc=table(d.receipt_plans,[['Тип',t('kind')],['Сумма',r=>money(r.amount)],['Статус',t('state')]],'Планов чеков нет');
  return renderReadOnly('<h3>Возвраты</h3>'+ref+'<h3>Планы чеков</h3>'+rc,'Тестовый платёж '+id.slice(0,12),'Сумма '+money(d.amount)+' · '+d.status+' · SHADOW');
 }
 if(name==='f4-bank')return modal('Тестовая строка банка','Никакого подключения к банку, реальная выписка не импортируется.',[
   fld('external_ref','Условный ID SIM:BANK:...','text',true,{value:'SIM:BANK:'+stamp()}),
   fld('direction','Направление','select',true,{options:[['IN','Зачисление'],['OUT','Возврат']]}),
   fld('amount','Сумма, ₽','number',true,{value:'2000'})
 ],b=>api(root+'/bank-lines',{method:'POST',body:JSON.stringify(b)}));
 if(name==='f4-match'){
  const payments=remember.f4?.payments||[];
  return modal('Тестовая сверка','Сопоставляются только имитированные банковские строки с завершёнными mock-операциями.',[
    fld('payment_id','Тестовый платёж','select',false,{options:payments.map(p=>[p.id,p.sim_order_ref+' · '+p.amount+' ₽'])}),
    fld('refund_id','UUID условного возврата (вместо платежа)','text',false,{value:''})
  ],b=>{if(b.refund_id)delete b.payment_id; else delete b.refund_id;return api(root+'/bank-lines/'+id+'/match',{method:'POST',body:JSON.stringify(b)})});
 }
 return false;
}

async function financeLedger(){
 const root=bo+'/finance/ledger', d=await api(root+'/overview');remember.f3=d;
 headerAction('Тестовый журнал','f3-new-book');
 const notice='<div class="notice"><b>ⓘ</b><div><b>Только F3 SHADOW.</b><br>Проводки являются моделированием и не подтверждают поступления банка, оплату, задолженность или выплату. Реальные заказы, СБП и закупки не подключены.</div></div>';
 if(!d.books.length){render('',notice+surface('Тестовые книги',empty('Тестовых книг пока нет'),'Создайте отдельную учебную книгу; это не бухгалтерская отчётность'));return}
 const book=remember.f3Book&&d.books.find(x=>x.id===remember.f3Book)?remember.f3Book:d.books[0].id;remember.f3Book=book;
 const [accounts,trial,entries,positions]=await Promise.all([api(root+'/books/'+book+'/accounts'),api(root+'/books/'+book+'/trial-balance'),api(root+'/books/'+book+'/entries'),api(root+'/books/'+book+'/settlement-positions')]);
 remember.f3Accounts=accounts;remember.f3Entries=entries;
 const picker='<div class="filters"><select id="f3-book-select">'+d.books.map(b=>'<option value="'+esc(b.id)+'" '+(b.id===book?'selected':'')+'>'+esc(b.code+' · '+b.name)+'</option>').join('')+'</select><button type="button" class="secondary-button" data-action="f3-open-book">Открыть</button><button type="button" class="secondary-button" data-action="f3-new-account">＋ Счёт</button><button type="button" class="secondary-button" data-action="f3-new-entry">＋ Проводка</button></div>';
 const balance=table(trial.accounts,[['Счёт',r=>'<b>'+esc(r.code)+'</b><div class="sub">'+esc(r.title)+'</div>'],['Тип',t('kind')],['Дебет',r=>money(r.debit)],['Кредит',r=>money(r.credit)],['Сальдо',r=>money(r.balance)]],'Счета ещё не созданы');
 const journals=table(entries,[['Дата',r=>esc(r.occurred_at.slice(0,16))],['Операция',r=>'<b>'+esc(r.event_kind)+'</b><div class="sub">'+esc(r.source_ref)+'</div>'],['Дебет / Кредит',r=>money(r.lines.filter(l=>l.side==='DEBIT').reduce((a,b)=>a+Number(b.amount),0))],['Обоснование',r=>esc(r.memo)],['Действия',r=>r.reverses_entry_id?'Сторно':act('Сторнировать','f3-reverse',r.id)+' '+act('Сверка','f3-reconcile',r.id)]],'Проводок нет');
 const debts=table(positions.positions,[['Счёт',t('code')],['Вид',t('settlement_role')],['Расчётное сальдо',r=>money(r.balance)]],'Расчётных позиций нет');
 render('',notice+surface('Тестовая книга',picker,'Все операции только в изолированном финансовом журнале')+surface('Оборотная ведомость',balance,'Дебет '+money(trial.total_debit)+' / Кредит '+money(trial.total_credit)+' · '+(trial.balanced?'Баланс сходится':'НАРУШЕН БАЛАНС'))+surface('Расчётные позиции продавцов/поставщиков',debts,'Не являются распоряжением о выплате')+surface('Проводки',journals,'История неизменяема; для исправления используется сторно'));
}
async function financeLedgerAction(name,id){
 const root=bo+'/finance/ledger',book=remember.f3Book;
 if(name==='f3-open-book'){remember.f3Book=$('#f3-book-select').value;return load()}
 if(name==='f3-new-book')return modal('Новая ТЕСТОВАЯ книга','Учетная книга SHADOW: не связана с банком или заказами.',[fld('code','Код (SIM-...)','text',true,{value:'SIM-TEST-'+Date.now().toString().slice(-7)}),fld('name','Название','text',true,{value:'Учебный финансовый журнал'})],b=>api(root+'/books',{method:'POST',body:JSON.stringify(b)}));
 if(!book)throw Error('Сначала создайте тестовую книгу.');
 if(name==='f3-new-account')return modal('Тестовый счёт','Код и назначение счёта после создания не редактируются; остаток вычисляется из проводок.',[fld('code','Код счёта','text',true,{value:'SELLER_PAYABLE'}),fld('title','Название','text',true,{value:'Расчёты с продавцом'}),fld('kind','Класс счёта','select',true,{options:[['ASSET','Активы'],['LIABILITY','Обязательства'],['INCOME','Доходы'],['EXPENSE','Расходы'],['EQUITY','Капитал']]}),fld('settlement_role','Назначение','select',true,{options:[['NONE','Обычный'],['SELLER_PAYABLE','Выплаты продавцу'],['SUPPLIER_PAYABLE','Долги поставщику'],['RECEIVABLE','Дебиторская задолженность']]})],b=>api(root+'/books/'+book+'/accounts',{method:'POST',body:JSON.stringify(b)}));
 if(name==='f3-new-entry'){
  const accounts=remember.f3Accounts||[];
  if(accounts.length<2)throw Error('Создайте не менее двух счетов для проводки.');
  const opts=accounts.map(a=>[a.id,a.code+' · '+a.title]);
  return modal('Тестовая двойная проводка','Сумма по дебету строго равна сумме по кредиту. Никакого денежного движения не будет.',[fld('source_ref','Источник SIM:','text',true,{value:'SIM:MANUAL:'+Date.now()}),fld('event_kind','Тип события','select',true,{options:[['PAYMENT','Условная оплата'],['COMMISSION','Комиссия площадки'],['BANK_FEE','Комиссия банка'],['SELLER_SETTLEMENT','Расчёт с продавцом'],['ADJUSTMENT','Корректировка']] }),fld('memo','Описание','text',true,{value:'Тестовая проводка'}),fld('debit_account','Дебет','select',true,{options:opts}),fld('credit_account','Кредит','select',true,{options:opts}),fld('amount','Сумма, ₽','text',true,{value:'2000'})],b=>{
   if(b.debit_account===b.credit_account)throw Error('Для теста выберите два разных счёта');
   return api(root+'/books/'+book+'/entries',{method:'POST',body:JSON.stringify({source_ref:b.source_ref,idempotency_key:b.source_ref.replace('SIM:','SIM-'),event_kind:b.event_kind,memo:b.memo,lines:[{account_id:b.debit_account,side:'DEBIT',amount:String(b.amount)},{account_id:b.credit_account,side:'CREDIT',amount:String(b.amount)}]})});
  });
 }
 if(name==='f3-reverse'){
  if(!confirm('Создать сторнирующую ТЕСТОВУЮ запись? История сохранится, реальный платёж не меняется.'))return;
  await api(root+'/books/'+book+'/entries/'+id+'/reverse',{method:'POST',body:JSON.stringify({idempotency_key:'SIM-REV-'+id,reason:'Корректировка тестовой операции'})});toast('Тестовая запись сторнирована');return load();
 }
 if(name==='f3-reconcile')return modal('Сверка · предварительный расчёт','Это НЕ запрос в банк и НЕ подтверждение реального платежа.',[fld('external_reference','Внешний идентификатор','text',true,{value:'SIM-REPORT-001'}),fld('expected_amount','Заявленная сумма, ₽','text',true,{value:'2000'})],async b=>{const r=await api(root+'/books/'+book+'/reconcile-preview',{method:'POST',body:JSON.stringify({entry_id:id,...b})});toast('Сверка '+r.status+': '+r.recorded_turnover+' ₽ / '+r.expected_amount+' ₽',r.status!=='MATCH');});
 return false;
}
async function financePricingAction(name,id){
 const root=bo+'/finance/pricing';
 if(name==='f2-new-rule')return modal('Правило наценки · черновик','Правило НЕ меняет действующие цены, используется только в симуляции.',[
   fld('name','Название'),fld('scope_kind','Область применения','select',true,{options:[['GLOBAL','Все продавцы (симуляция)']]}),fld('valid_from','Дата начала','date',true,{value:'2026-10-20'}),fld('markup_percent','Наценка, %','number',true,{value:'25'}),fld('fixed_markup','Надбавка, ₽','number',true,{value:'0'}),fld('floor_price','Минимальная цена, ₽','number',true,{value:'0'}),fld('min_contribution','Мин. денежный остаток, ₽','number',true,{value:'0'}),fld('rounding_mode','Цена для покупателя','select',true,{options:[['EXACT_RUB','Точные рубли (по умолчанию)'],['SMART_UP','Умное округление вверх']]}),fld('rounding_max_uplift_pct','Лимит прибавки, % (пусто — 2% до 500 ₽, иначе 1%)','number',false),fld('rounding_tiers','Диапазоны: JSON (необязательно)','textarea',false,{value:''})
 ],b=>{if(b.rounding_tiers){try{b.rounding_tiers=JSON.parse(b.rounding_tiers)}catch{throw Error('Диапазоны должны быть корректным JSON-массивом')} }return api(root+'/rules',{method:'POST',body:JSON.stringify(b)})});
 if(name==='f2-new-fee')return modal('Тариф · черновик','Введите реальный договорный тариф: верхняя граница ЦБ не равна тарифу вашего банка.',[
   fld('name','Название'),fld('fee_kind','Тип','select',true,{options:[['SBP','СБП банка'],['MARKETPLACE','Комиссия площадки'],['LOGISTICS','Логистика'],['OTHER','Прочие услуги']]}),fld('valid_from','Дата начала','date',true,{value:'2026-10-20'}),fld('percent','Процент, %','number',true,{value:'0'}),fld('fixed_amount','Фиксированная комиссия, ₽','number',true,{value:'0'}),fld('cap_amount','Лимит за операцию, ₽ (пусто — без лимита)','number',false),fld('evidence_reference','Договор / тариф банка')
 ],b=>api(root+'/fees',{method:'POST',body:JSON.stringify(b)}));
 if(name==='f2-retire-rule'||name==='f2-retire-fee'){
   if(!confirm('Архивировать правило? История сохранится, повторное использование прекратится.'))return;
   await api(root+(name==='f2-retire-rule'?'/rules/':'/fees/')+id+'/retire',{method:'POST'});toast('Архивировано');return load();
 }
 if(name==='f2-preview-own'||name==='f2-preview-agent'){
   const own=name==='f2-preview-own';const ruleOptions=(remember.f2?.pricing_rules||[]).filter(x=>x.state==='draft'&&x.scope_kind==='GLOBAL').map(x=>[x.id,x.name+' · '+x.markup_percent+'%']);
   const feeOptions=(remember.f2?.fee_schedules||[]).filter(x=>x.state==='draft');
   const fields=[fld('operation_date','Дата','date',true,{value:'2026-10-20'}),fld('source_gross',own?'Закупка с НДС, ₽':'Цена покупателю, ₽','number',true,{value:own?'1000':'2000'})];
   if(own)fields.push(fld('markup_percent','Наценка, %','number',true,{value:'25'}),fld('rounding_mode','Цена для покупателя','select',true,{options:[['EXACT_RUB','Точные рубли'],['SMART_UP','Умное округление вверх']]}),fld('rounding_max_uplift_pct','Макс. надбавка от округления, %','number',false),fld('price_ceiling','Ценовой потолок, ₽ (при наличии)','number',false),fld('pricing_rule_id','Сохранённое правило (при выборе заменяет наценку)','select',false,{options:ruleOptions}));
   else fields.push(fld('marketplace_percent','Комиссия платформы, %','number',true,{value:'10'}),fld('marketplace_fee_schedule_id','Сохранённый тариф комиссии','select',false,{options:feeOptions.filter(x=>x.fee_kind==='MARKETPLACE').map(x=>[x.id,x.name])}),fld('bank_fee_bearer','Кто оплачивает комиссию банка','select',true,{options:[['PLATFORM','Платформа'],['SELLER','Продавец']]}));
   fields.push(fld('last_mile_delivery','Расход на доставку, ₽','number',true,{value:'0'}),fld('packaging','Упаковка, ₽','number',true,{value:'0'}),fld('sbp_percent','СБП, %','number',true,{value:'0'}),fld('sbp_fee_schedule_id','Сохранённый тариф банка','select',false,{options:feeOptions.filter(x=>x.fee_kind==='SBP').map(x=>[x.id,x.name])}),fld('vat_scheme','Модель НДС (гипотеза)','select',false,{options:[['STANDARD','НДС 22%'],['USN_5','НДС 5%'],['USN_7','НДС 7%'],['EXEMPT','Без НДС']] }));
   return modal(own?'Расчёт собственной продажи':'Расчёт агентской продажи','Предварительная симуляция — никакие цены и выплаты не изменятся.',fields,async b=>{const r=await api(root+'/preview',{method:'POST',body:JSON.stringify({sale_model:own?'OWN':'AGENCY',...b})});remember.f2Quote=r;toast('Расчёт готов, см. таблицу на странице.');});
 }
 return false;
}
async function financeAction(name,id){
 const fbase=bo+'/finance';
 if(name==='fin-new-entity'){
   const merchants=await api(wb+'/merchants');
   return modal('Новое юридическое лицо','Создаётся только черновик. Не предполагаем ООО, ИП или режим автоматически.',[
     fld('name','Наименование'),fld('entity_type','Тип','select',true,{options:[['company','ООО/организация'],['sole_proprietor','ИП'],['individual','Физлицо (без налогового профиля)'],['self_employed','Самозанятый (отдельная проверка)']]}),
     fld('merchant_id','Продавец в системе','select',false,{options:merchants.map(m=>[m.id,m.name])}),
     fld('inn','ИНН (необязательно)','text',false),fld('kpp','КПП (только для организаций)','text',false)
   ],async b=>{await api(fbase+'/legal-entities',{method:'POST',body:JSON.stringify(b)})});
 }
 if(name==='fin-new-profile'){
   const e=remember.finance?.entities.find(x=>x.id===id);
   if(!e)throw Error('Юридическое лицо не найдено. Обновите страницу.');
   return modal('Налоговый профиль — черновик',e.name+' · режим должен быть подтверждён документально',[
     fld('regime','Режим','select',true,{options:[['OSNO','ОСНО'],['USN_INCOME','УСН Доходы'],['USN_PROFIT','УСН Доходы минус расходы'],['AUSN_INCOME','АУСН Доходы'],['AUSN_PROFIT','АУСН Доходы минус расходы']]}),
     fld('vat_scheme','НДС','select',true,{options:[['UNRESOLVED','Не определено'],['STANDARD','Общий порядок'],['USN_5','УСН НДС 5%'],['USN_7','УСН НДС 7%'],['EXEMPT','Освобождение / АУСН внутренние операции']]}),
     fld('valid_from','Начало действия','date'),fld('valid_until','Конец действия (необязательно)','date',false),
     fld('evidence_reference','Документ-основание (при наличии)','text',false)
   ],b=>api(fbase+'/tax-profiles',{method:'POST',body:JSON.stringify({entity_id:id,...b})}));
 }
 if(name==='fin-preview'){
   return modal('НДС · пример расчёта','Пример не отражается в заказах и не подтверждает выбор налогового режима.',[
     fld('gross_amount','Сумма с НДС, ₽','number',true,{value:'1220'}),
     fld('operation_date','Дата операции','date',true,{value:new Date().toISOString().slice(0,10)}),
     fld('sale_model','Модель продажи','select',true,{options:[['OWN','Собственная'],['AGENCY','Агентская']]}),
     fld('operation_kind','Тип операции','select',true,{options:[['GOODS','Реализация товара'],['AGENCY_FEE','Агентская комиссия'],['ADVANCE','Аванс'],['REFUND','Возврат']]}),
     fld('vat_scheme','НДС','select',true,{options:[['STANDARD','22% (общая)'],['USN_5','5% (УСН)'],['USN_7','7% (УСН)'],['EXEMPT','Без НДС']]})
   ],async b=>{const r=await api(fbase+'/tax/preview',{method:'POST',body:JSON.stringify(b)});toast('Симуляция: база '+r.net_amount+' ₽, НДС '+r.vat_amount+' ₽. Результат не записан.');});
 }
 if(name==='fin-rules'){
   const r=await api(fbase+'/tax-rules/2026');
   toast('НДС 22%; УСН освобождение '+r.threshold_rub.usn_vat_exempt+' ₽; ставки 5%/7% зависят от условий и доходов.');
   return;
 }
 return false;
}

async function financeTaxRegisters(){
 const root=bo+'/finance/tax-registers-sim',d=await api(root+'/overview');remember.f5=d;
 const book=remember.f5Book && d.books.some(x=>x.id===remember.f5Book)?remember.f5Book:d.books[0]?.id;
 remember.f5Book=book;
 headerAction('Тестовая налоговая книга','f5-new-book');
 let rows=[];if(book)rows=(await api(root+'/books/'+book+'/rows')).rows;
 const notice='<div class="notice"><b>ⓘ</b><div><b>F5 — только симуляция. НЕ официальный КУДиР, книга покупок/продаж или налоговая декларация.</b><br>Все документы SIM:F5:DOC, налоговый профиль не подтверждён, налоговые обязательства и реальные операции не формируются.</div></div>';
 const tiles='<div class="tile-grid">'+tile('Тестовых книг',d.counts.books,'▧')+tile('Документов-симуляций',d.counts.documents,'▤')+tile('Строк регистров',d.counts.rows,'≡')+'</div>';
 const choices='<div class="filters"><select id="f5-book-select">'+d.books.map(b=>`<option value="${esc(b.id)}" ${b.id===book?'selected':''}>${esc(b.book_ref+' · '+b.actor_role)}</option>`).join('')+'</select><button type="button" class="secondary-button" data-action="f5-open-book">Открыть</button><button type="button" class="secondary-button" data-action="f5-new-doc">＋ Источник</button><button type="button" class="secondary-button" data-action="f5-new-row">＋ Запись</button></div>';
 const documents=table(d.documents,[['Тестовый документ',t('document_ref')],['Тип',t('kind')],['Дата',t('document_date')]],'Пока нет тестовых документов');
 const entries=table(rows,[['Дата',t('operation_date')],['Операция',t('event_kind')],['Сумма',r=>money(r.gross_amount)],['НДС · расчёт',r=>r.vat_preview===null?'Не определён':money(r.vat_preview)],['Ключ',t('request_key')]],'Налоговые события-симуляции отсутствуют');
 const exports=book?'<div class="filters">'+d.formats.map(f=>`<button type="button" class="secondary-button" data-action="f5-export" data-id="${esc(f)}">${esc(f.replaceAll('_',' '))}</button>`).join('')+'</div>':empty('Сначала создайте тестовую книгу');
 render('',notice+tiles+surface('Тестовая книга',choices,'Не привязана к реально зарегистрированному налогоплательщику')+surface('Документы-основания SIM',documents,'Только синтетические источники; реальные УПД не загружаются')+surface('Налоговые события',entries,'НДС носит иллюстративный характер')+surface('Экспорт для проверки структуры',exports,'CSV с пометкой SHADOW_NOT_FOR_FILING, не готов для ФНС или 1С'));
}
async function financeTaxRegistersAction(name,id){
 const root=bo+'/finance/tax-registers-sim', stamp=()=>Date.now().toString();
 if(name==='f5-open-book'){remember.f5Book=$('#f5-book-select').value;return load()}
 if(name==='f5-new-book')return modal('Новая тестовая налоговая книга','Отдельная книга для роли PLАТФОРМЫ или ПРОДАВЦА. Налоговый режим будет UNRESOLVED.',[
  fld('book_ref','Код SIM:F5:BOOK:...','text',true,{value:'SIM:F5:BOOK:'+stamp()}),
  fld('actor_role','Кому принадлежит доход','select',true,{options:[['PLATFORM','Платформа / собственный магазин'],['SELLER','Сторонний продавец']]}),
  fld('period_start','Начало периода','date',true,{value:'2026-10-01'}),fld('period_end','Конец периода','date',true,{value:'2026-12-31'})
 ],b=>api(root+'/books',{method:'POST',body:JSON.stringify(b)}));
 if(name==='f5-new-doc')return modal('Синтетический первичный документ','Это не фактический УПД, чек или отчёт агента.',[
  fld('document_ref','Код SIM:F5:DOC:...','text',true,{value:'SIM:F5:DOC:'+stamp()}),fld('document_date','Дата','date',true,{value:'2026-10-20'}),
  fld('kind','Тип','select',true,{options:[['SALE','Реализация'],['PURCHASE','Закупка'],['AGENT_REPORT','Отчёт агента'],['ADVANCE','Аванс'],['REFUND','Возврат'],['EXPENSE','Расход']]}),
  fld('memo','Описание','text',true,{value:'Тестовый источник, без юридической силы'})
 ],b=>api(root+'/documents',{method:'POST',body:JSON.stringify(b)}));
 if(name==='f5-new-row'){
  if(!remember.f5Book)throw Error('Сначала создайте тестовую книгу');
  const docs=remember.f5?.documents||[];if(!docs.length)throw Error('Создайте тестовый документ');
  const role=remember.f5.books.find(b=>b.id===remember.f5Book)?.actor_role;
  const opts=(role==='SELLER'?['AGENCY_SELLER_SALE','PURCHASE','ADVANCE','REFUND','EXPENSE']:['OWN_SALE','AGENCY_PLATFORM_FEE','PURCHASE','ADVANCE','REFUND','EXPENSE']).map(x=>[x,x]);
  return modal('Запись в тестовом регистре','Только синтетические данные, для возврата понадобится ссылка на исходную запись.',[
    fld('request_key','Ключ SIM:F5:ROW:...','text',true,{value:'SIM:F5:ROW:'+stamp()}),
    fld('document_id','Тестовый документ','select',true,{options:docs.map(d=>[d.id,d.document_ref+' · '+d.kind])}),
    fld('event_kind','Вид события','select',true,{options:opts}),
    fld('operation_date','Дата операции','date',true,{value:'2026-10-20'}),
    fld('gross_amount','Сумма, ₽','number',true,{value:'2000'}),
    fld('vat_scheme','Модель НДС для симуляции','select',true,{options:[['UNRESOLVED','Не подтверждена'],['STANDARD','Основная 22%'],['USN_5','УСН 5%'],['USN_7','УСН 7%'],['EXEMPT','Без НДС']]}),
    fld('correction_of_id','UUID исходной записи (только для REFUND)','text',false)
  ],b=>api(root+'/books/'+remember.f5Book+'/rows',{method:'POST',body:JSON.stringify(b)}));
 }
 if(name==='f5-export'){
  if(!remember.f5Book)throw Error('Выбери тестовую книгу');
  const path=root+'/books/'+remember.f5Book+'/export/'+encodeURIComponent(id);
  const blob=await apiBlob(path);const url=URL.createObjectURL(blob);
  try{const a=document.createElement('a');a.href=url;a.download='SIM_F5_'+id+'_2026.csv';document.body.appendChild(a);a.click();a.remove()}finally{setTimeout(()=>URL.revokeObjectURL(url),1000)}
  toast('Скачан предварительный CSV. Не для налоговой отчётности.');return;
 }
 return false;
}


const autoEndpoint='/api/v1/admin/draft-materialization/automation';
async function catalogAutomation(){
  const [data,summary]=await Promise.all([api(autoEndpoint+'/runs?limit=15'),api(autoEndpoint+'/statistics')]);
  const active=(data.items||[]).find(r=>r.status==='processing'||r.status==='queued');
  const form=`<div class="filters"><label>Число кандидатов <input type="number" id="auto-limit" min="1" max="350000" value="1000"></label><label>Проверить старые карточки <input type="number" id="auto-legacy" min="0" max="2000" value="200"></label><button class="secondary-button" data-action="auto-preview" ${active?'disabled':''}>Проверить без создания товаров</button><button class="primary" data-action="auto-run" ${active?'disabled':''}>Автоматически создать черновики</button><button class="secondary-button" data-action="auto-refresh">Обновить</button></div>`;
  const rows=table(data.items||[],[['Задание',r=>'<span class="sub">'+esc(r.id.slice(0,13))+'…</span>'],['Режим',r=>esc(r.automation?.mode||'—')],['Этап',r=>esc(r.automation?.stage||'—')],['Статус',b('status')],['Кандидатов',r=>num(r.scanned)],['Черновики',r=>num(r.created_drafts)],['Категорий восстановлено',r=>num(r.automation?.legacy_recovered)],['Проверка',r=>num(r.reviewed)],['Действие',r=>r.status==='failed'?act('Продолжить','auto-resume',r.id):'—']],'Заданий пока нет');
  render('',`<div class="notice"><b>Закрытые черновики</b><div>Все операции выполняются автоматически в worker и фиксируются в журнале. Публикация карточек выполняется только вручную, по одной. Режим проверки сохраняет Preflight, но не создаёт или не изменяет товары. Активная ревизия: ${esc(summary.rule_revision)}.</div></div>`+surface('Настройки выполнения',form,'Отдельное задание автоматически продолжит обработку после очередной порции')+surface('Состояние Preflight',`<div class="quick-grid">${Object.entries(summary.preflight_statuses||{}).map(([k,v])=>`<div class="quick-card"><b>${num(v)}</b><small>${esc(k)}</small></div>`).join('')}</div>`)+surface('История автоматизации',rows,'Данные из PostgreSQL, защита от одновременного запуска'));
}
async function autoAction(name,id){
 if(name==='auto-refresh')return load();
 if(name==='auto-resume'){
   if(!confirm('Возобновить задание с сохранённого курсора?'))return;
   await api(autoEndpoint+'/runs/'+encodeURIComponent(id)+'/resume',{method:'POST'});toast('Возобновление поставлено в очередь');return load();
 }
 const amount=Number($('#auto-limit')?.value), legacy=Number($('#auto-legacy')?.value);
 if(!Number.isInteger(amount)||amount<1||amount>350000||!Number.isInteger(legacy)||legacy<0||legacy>2000)throw Error('Некорректные ограничения задания');
 const mode=name==='auto-run'?'drafts':'preview';
 if(mode==='drafts'&&!confirm(`Создать только черновики (до ${amount} кандидатов), автоматически восстановить допустимые категории? НИЧЕГО НЕ ПУБЛИКОВАТЬ.`))return;
 await api(autoEndpoint+'/runs',{method:'POST',body:JSON.stringify({mode,limit:amount,legacy_limit:legacy,confirm_draft_only:mode==='drafts'})});
 toast('Задание поставлено в фоновую очередь');return load();
}


// Supplier Price Feeds console: operator-controlled staged uploads only.
// Uploading and parsing a feed DOES NOT activate selling prices or publish products.
const sfEndpoint='/api/v1/admin/supplier-feeds';
const sfSuppliers={rossko:'ROSSKO · ZIP/XLSX',partkom:'PART-KOM · CSV'};
function sfSummary(batch){return `${num(batch.rows_valid)} корректных / ${num(batch.rows_rejected)} ошибок / ${num(batch.rows_duplicates)} дублей`}
async function supplierFeeds(){
 headerAction('','');
 const [hist,sourceResp,scopeResp,policyResp,syncResp]=await Promise.all([api(sfEndpoint+'/imports?limit=40'),api(sfEndpoint+'/sources'),api(sfEndpoint+'/pricing/scopes'),api(sfEndpoint+'/pricing/policies'),api(sfEndpoint+'/catalog-syncs?limit=30')]);
 const sources=sourceResp.items||[];
 const upload=`<form id="sf-upload" class="surface-body"><div class="filters"><label>Поставщик <select id="sf-supplier" required><option value="rossko">ROSSKO — ZIP/XLSX</option><option value="partkom">PART-KOM — CSV</option></select></label><label>Файл прайса (до 48 МиБ) <input id="sf-file" type="file" accept=".zip,.xlsx" required></label><button type="submit" class="primary" id="sf-submit">Загрузить и обработать</button><button type="button" class="secondary-button" data-action="sf-refresh">Обновить</button></div><p class="sub">Файл проверяется сервером, сохраняется в новой версии, затем worker разбирает закупочные цены, остатки, склады и описания. Результат — <b>staged</b>, без переключения активного прайса и без публикации карточек. Поддерживаются только проверенные форматы ROSSKO и PART-KOM.</p></form>`;
 const sourceRows=table(sources,[['Источник',r=>esc((sfSuppliers[r.supplier]||r.supplier).split(' · ')[0])],['Активная версия',r=>r.active_batch_id?esc(r.active_batch_id.slice(0,12))+'…':'Не выбрана'],['Закупочная цена',r=>r.commercial_verified&&r.price_kind==='purchase'&&r.currency==='RUB'?badge('confirmed'):badge('review')],['НДС по файлу',r=>esc(r.tax_status||'unknown')],['Возраст файла',r=>esc(r.max_age_hours)+' ч'],['Действие',r=>act('Проверить условия цены','sf-commercial',r.supplier)]],'Источники ещё не загружены');
 const rows=table(hist.items||[],[['Поставщик',r=>esc(r.supplier.toUpperCase())],['Файл / время',r=>`<b>${esc(r.original_name)}</b><div class="sub">${esc((r.created_at||'').slice(0,16).replace('T',' '))} UTC</div>`],['Статус',r=>badge(r.status)],['Строки',r=>sfSummary(r)],['Сопоставлено',r=>num(r.rows_matched)],['Действия',r=>[
 (r.status==='uploaded'||r.status==='failed'?act('Обработать','sf-process',r.id):''),
 (['staged','published'].includes(r.status)?act('Цены и остатки','sf-offers',r.id)+' '+act('Сравнить версии','sf-diff',r.id)+' '+act('Склады','sf-warehouses',r.id):''),
 (r.status==='staged'?act('Активировать прайс','sf-activate',r.id):''),
 (['staged','published'].includes(r.status)?act('Каталог: состояние','sf-sync',r.id):''),
 act('Подробнее','sf-details',r.id)
 ].filter(Boolean).join(' ')]] ,'Прайсы пока не загружались');
 const scopes=scopeResp.items||[], policies=policyResp.items||[], syncs=syncResp.items||[];
 const pricingForm=`<form id="sf-price" class="surface-body"><p class="sub">Стоимость закупки из активного прайса + расходы до приёмки + упаковка + резерв + минимальная прибыль + комиссия СБП. Все расходы указываются <b>в рублях на единицу</b>. Только утверждённые ставки используются в предварительных ценах. При отсутствии тарифа цена не показывается.</p><div class="filters"><label>Склад · аккаунт · адрес приёмки <select id="sf-price-scope" required>${scopes.map((m,i)=>`<option value="${i}">${esc(m.supplier_code+' | '+m.warehouse_code+' | '+m.account_id.slice(0,8)+' | '+m.destination_id.slice(0,8))}</option>`).join('')}</select></label>${[['markup_percent','Наценка, %'],['supplier_delivery','Доставка поставщика, ₽/шт'],['packaging','Упаковка, ₽/шт'],['other_costs','Прочие расходы, ₽/шт'],['tax_reserve','Резерв на налоговые расходы, ₽/шт'],['sbp_percent','Комиссия СБП, %'],['sbp_fixed','СБП фикс., ₽'],['min_contribution','Минимальная прибыль, ₽/шт']].map(([k,label])=>`<label>${label}<input name="${k}" type="number" min="0" step="0.01" required></label>`).join('')}<label>Основание тарифов/договора <input name="evidence_reference" minlength="12" maxlength="300" required></label><button type="submit" class="primary" ${scopes.length?'':'disabled'}>Сохранить проект тарифа</button></div><p class="sub">После сохранения отдельно утвердите тариф в списке ниже. Изменение тарифа автоматически отзывает утверждение.</p></form>`;
 const policyRows=table(policies,[['Поставщик/склад',r=>esc(r.supplier_code)+' '+esc(r.warehouse_code)],['Аккаунт',r=>esc(r.account_id.slice(0,8))],['Наценка',r=>esc(r.markup_percent)+'%'],['СБП',r=>esc(r.sbp_percent)+'%'],['Версия',r=>num(r.version)],['Статус',r=>badge(r.approved?'approved':'draft')],['Действия',r=>r.approved?'':act('Утвердить','sf-price-approve',r.id)]],'Тарифов пока нет');
 const syncRows=table(syncs,[['Прайс',r=>esc(r.batch_id.slice(0,10))],['Статус',r=>badge(r.status)],['Этап',r=>esc(r.stage)],['Проверено',r=>num(r.scanned)],['Создано черновиков',r=>num(r.created_drafts)],['На проверке',r=>num(r.reviewed)],['Действие',r=>['failed','paused'].includes(r.status)?act('Возобновить','sf-sync-resume',r.batch_id):'']],'Автоматические задания появятся после импорта новых прайсов');
 render('',`<div class="notice"><b>Автоматический конвейер</b><div><b>Загрузка → разбор → staged → классификация новых артикулов → безопасные черновики</b>; активация закупочного прайса — отдельно. Цена на витрине рассчитывается только по утверждённым тарифам аккаунта, адреса приёмки и склада. Без подтверждения через API оплата и заказ недоступны. Товары не публикуются.</div></div>`+surface('Новый прайс-лист',upload,'Файлы не должны содержать пароли и ключи API')+surface('Источники и коммерческий контекст',sourceRows,'Для расчёта нужен подтверждённый закупочный смысл, RUB и режим НДС')+surface('Тарифы продажной цены',pricingForm,'Каждая ставка относится к конкретному складу и аккаунту')+surface('Утверждённые и черновые тарифы',policyRows,'Изменение затрат отзывает подтверждение')+surface('Автоматическая синхронизация каталога',syncRows,'Сканируются только позиции нового прайса; публикация товара не выполняется')+surface('Версии закупочных прайсов',rows,'Цены разных выгрузок хранятся раздельно; новый файл не перезаписывает историю'));
}
async function supplierFeedUpload(form){
 const supplier=$('#sf-supplier',form)?.value,file=$('#sf-file',form)?.files?.[0],submit=$('#sf-submit',form);
 if(!sfSuppliers[supplier]||!file)throw Error('Выберите поддерживаемого поставщика и файл');
 const ext=file.name.split('.').pop().toLowerCase();
 if((supplier==='rossko'&&!['zip','xlsx'].includes(ext))||(supplier==='partkom'&&ext!=='csv'))throw Error('Формат файла не соответствует поставщику');
 if(file.size===0||file.size>48*1024*1024)throw Error('Размер файла должен быть от 1 байта до 48 МиБ');
 submit.disabled=true;
 try{
  const body=new FormData();body.append('file',file,file.name);
  const uploaded=await api(sfEndpoint+'/'+supplier+'/uploads',{method:'POST',body});
  if(uploaded.already_uploaded){toast('Этот файл уже зарегистрирован: '+uploaded.status);}
  else{await api(sfEndpoint+'/imports/'+encodeURIComponent(uploaded.id)+'/process',{method:'POST'});toast('Прайс загружен и поставлен на разбор: '+uploaded.id.slice(0,8));}
  await load();
 }finally{submit.disabled=false}
}
async function sfAction(name,id){
 if(name==='sf-refresh')return load();
 if(name==='sf-price-approve'){
   const p=(await api(sfEndpoint+'/pricing/policies')).items.find(r=>r.id===id);
   if(!p)throw Error('Тариф не найден');
   if(!confirm('Подтвердить коммерческие расходы и налоги по документу '+p.evidence_reference+'? Цена будет ориентировочной; перед оплатой обязательна проверка API.'))return;
   await api(sfEndpoint+'/pricing/policies/'+encodeURIComponent(id)+'/approve',{method:'POST',body:JSON.stringify({expected_version:p.version,confirm_contract_costs:true})});toast('Тариф утверждён');return load();
 }
 if(name==='sf-sync' || name==='sf-sync-resume'){
   const path=sfEndpoint+'/imports/'+encodeURIComponent(id)+'/catalog-sync';
   if(name==='sf-sync-resume'){
      if(!confirm('Возобновить синхронизацию без публикации товаров?'))return;
      await api(path+'/resume',{method:'POST'});toast('Синхронизация возобновлена');return load();
   }
   let v;
   try{v=await api(path)}catch(e){if(!String(e.message).includes('Catalog sync not created'))throw e;if(!confirm('Этот прайс загружен до v0.31.5. Поставить синхронизацию его артикулов в очередь? Будут создаваться ТОЛЬКО черновики, активные цены не переключатся.'))return;v=await api(path,{method:'POST'})}
   return renderReadOnly(table([v],[['Состояние',t('status')],['Этап',t('stage')],['Сканировано',t('scanned')],['Создано черновиков',t('created_drafts')],['Модерация',t('reviewed')],['Привязка предложений',t('linked')],['Сообщение',t('message')]]),'Синхронизация прайса','Без активации закупочных цен и без публикации товаров');
 }
 if(name==='sf-commercial'){
   if(!sfSuppliers[id])throw Error('Неподдерживаемый поставщик');
   const selected=prompt('Подтверди по договору или прайсу режим НДС: included (НДС включён), excluded (без НДС), unknown (не установлено). ОТМЕНА — не подтверждать.');
   if(selected===null)return;
   if(!['included','excluded','unknown'].includes(selected))throw Error('Допустимы included, excluded или unknown');
   if(!confirm('Подтверждаете, что цена из файлов '+id.toUpperCase()+' действительно ваша ЗАКУПОЧНАЯ цена в RUB по договору и указанный режим НДС проверен (unknown — сведений нет)? Это не подтверждает наличие у поставщика.'))return;
   await api(sfEndpoint+'/sources/'+id+'/commercial-scope',{method:'POST',body:JSON.stringify({price_kind:'purchase',currency:'RUB',tax_status:selected,verified:true})});
   toast('Коммерческий контекст сохранён; активные прайсы не переключались');return load();
 }
 const root=sfEndpoint+'/imports/'+encodeURIComponent(id);
 if(name==='sf-process'){
   if(!confirm('Поставить файл в очередь обработки? Текущий активный прайс не изменится.'))return;
   await api(root+'/process',{method:'POST'});toast('Обработка поставлена в очередь');return load();
 }
 if(name==='sf-details'){
   const batchInfo=await api(root);return renderReadOnly(table([batchInfo],[['Файл',t('original_name')],['Поставщик',t('supplier')],['Статус',b('status')],['Строк всего',t('rows_total')],['Проверено',t('rows_valid')],['Ошибок',t('rows_rejected')],['Дублей',t('rows_duplicates')],['Сопоставлено с карточками',t('rows_matched')]])+`<div class="surface-body"><b>Причины ошибок</b><pre style="white-space:pre-wrap">${esc(batchInfo.errors_sample||'Нет')}</pre><p class="sub">SHA-256: ${esc(batchInfo.sha256)}</p></div>`,'Версия прайса','Данные импорта. Никакой публикации товаров.');
 }
 if(name==='sf-offers'){
   const o=await api(root+'/offers?limit=40');return renderReadOnly(table(o.items||[],[['Бренд / артикул',r=>esc(r.brand)+' '+esc(r.article)],['Склад',t('warehouse')],['Закупочная цена, ₽',r=>money(r.price)],['Остаток поставщика',r=>esc(r.stock_raw)+(r.is_lower_bound?' (нижняя граница)':'')],['Товар',r=>r.matched_product_id?esc(r.matched_product_id.slice(0,8)):'Не сопоставлен']]),'Прайс: закупочные цены','Показаны первые 40 строк версии. Это ещё не предложения, подтверждённые для продажи.');
 }
 if(name==='sf-warehouses'){
   const o=await api(root+'/warehouses');return renderReadOnly(table(o.warehouses||[],[['Склад',t('code')],['Предложений',r=>num(r.rows)],['Связано с товаром',r=>num(r.matched)]]),'Склады прайса','Привязки к SupplierAccount и адресу приёмки утверждаются отдельно.');
 }
 if(name==='sf-diff'){
   const d=await api(root+'/diff');return renderReadOnly(`<div class="surface-body">${d.comparison==='no_previous_published_file'?'<b>Предыдущей активной опубликованной версии нет.</b>':table([d],[['В новой версии',r=>num(r.rows_new)],['Добавлено',r=>num(r.added)],['Удалено',r=>num(r.removed)],['Изменили цену',r=>num(r.price_changed)],['Изменили остаток',r=>num(r.stock_changed)]])}<p class="sub">Изменения цены и остатка могут относиться к одной строке; суммы не складываются. Сравнение по ключу строки и складу.</p></div>`,'Изменения прайса','Только чтение; активный прайс не меняется.');
 }
 if(name==='sf-activate'){
   const [batch,sr]=await Promise.all([api(root),api(sfEndpoint+'/sources')]);
   const source=(sr.items||[]).find(x=>x.supplier===batch.supplier);
   if(batch.status!=='staged')throw Error('Для активации нужна версия staged');
   if(!source||!source.commercial_verified||source.price_kind!=='purchase'||source.currency!=='RUB')throw Error('Смысл закупочной цены и RUB ещё не подтверждён. Сначала проверьте договор, коммерческий контекст, склады и API-аккаунты.');
   const generated_at=prompt('Введите ПОДТВЕРЖДЁННОЕ время формирования файла с часовым поясом, например 2026-10-22T08:00:00+04:00. Не подставляйте текущее время вместо неизвестного.');
   if(!generated_at)return;
   if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$/.test(generated_at))throw Error('Укажите ISO 8601 время с часовым поясом');
   if(!confirm('Активировать ТОЛЬКО версию прайса '+batch.original_name+'? Файл содержит ЗАКУПОЧНЫЕ цены, это НЕ публикация товаров и НЕ подтверждение наличия API.'))return;
   await api(root+'/publish',{method:'POST',body:JSON.stringify({generated_at})});toast('Активная версия закупочного прайса переключена; товары не опубликованы');return load();
 }
}
const pages={'supplier-feeds':supplierFeeds,'catalog-automation':catalogAutomation,analytics,finance,'finance-registers':financeTaxRegisters,'finance-pricing':financePricing,'finance-ledger':financeLedger,'finance-payments':financePayments,dashboard,monitor,'receiving-readiness':receivingReadiness,catalog,offers,inventory,merchants,stores,used,nodes,routes,suppliers,accounts,destinations,orders,jobs,purchases,refunds};
let catalogEditor=null;
// Modal forms use exactly the existing validated API contracts. IDs are selected from lists.
const fld=(name,label,type='text',required=true,other={})=>({name,label,type,required,...other});
const f=(...fields)=>fields;
async function options(path,field='name',labelField=''){const rows=await api(wb+'/'+path);return rows.map(x=>[x.id,labelField?`${x[field]} · ${x[labelField]}`:x[field]])}
const specs={
 'new-brand':{title:'Новый производитель',endpoint:bo+'/brands',fields:()=>f(fld('name','Название'),fld('slug','URL-идентификатор'))},
 'new-product':{title:'Новый товар',endpoint:bo+'/products',fields:async()=>f(fld('brand_id','Производитель','select',true,{options:await options('brands')}),fld('article','Артикул'),fld('name','Название'),fld('description','Описание','textarea',false))},
 'new-merchant':{title:'Новый продавец',endpoint:mp+'/merchants',fields:()=>f(fld('name','Название'),fld('slug','URL-идентификатор'),fld('is_platform_owner','Основной продавец','checkbox',false))},
 'new-store':{title:'Новый магазин',endpoint:mp+'/stores',fields:async()=>f(fld('merchant_id','Продавец','select',true,{options:await options('merchants')}),fld('name','Название магазина'),fld('slug','URL-идентификатор'))},
 'new-node':{title:'Новый склад или ПВЗ',endpoint:mp+'/nodes',fields:async()=>f(fld('merchant_id','Продавец','select',true,{options:await options('merchants')}),fld('store_id','Магазин (необязательно)','select',false,{options:await options('stores')}),fld('code','Код точки'),fld('name','Название'),fld('kind','Тип','select',true,{options:[['warehouse','Склад'],['pickup','ПВЗ'],['warehouse_pickup','Склад + ПВЗ']]}),fld('city','Город'),fld('address','Адрес'),fld('timezone_name','Часовой пояс','text',true,{value:'Europe/Moscow'}),fld('receiving_contact_name','ФИО получателя поставок','text',false),fld('receiving_contact_phone','Телефон получателя поставок','text',false),fld('receiving_contact_comment','Комментарий contact (может быть пустым)','textarea',false))},
 'new-route':{title:'Новый маршрут',endpoint:mp+'/routes',fields:async()=>f(fld('source_node_id','Откуда','select',true,{options:await options('nodes','name','city')}),fld('target_node_id','Куда','select',true,{options:await options('nodes','name','city')}),fld('transit_days','Дней доставки','number',true,{value:'1'}),fld('handling_days','Обработка, дней','number',true,{value:'0'}),fld('fee','Стоимость, ₽','number',true,{value:'0'}))},
 'new-supplier':{title:'Новый поставщик',endpoint:bo+'/suppliers',fields:()=>f(fld('code','Код (например, new-supplier)'),fld('name','Название поставщика'))},
 'new-account':{title:'Новый API-аккаунт',endpoint:mp+'/supplier-accounts',fields:async()=>f(fld('supplier_id','Поставщик','select',true,{options:await options('suppliers')}),fld('merchant_id','Продавец','select',true,{options:await options('merchants')}),fld('label','Название аккаунта'),fld('credential_profile','Профиль переменных окружения','text',true,{value:'default'}),fld('requests_per_hour','Лимит запросов/час','number',true,{value:'30'}),fld('timeout_seconds','Тайм-аут, сек','number',true,{value:'20'}))},
 'new-destination':{title:'Привязать адрес поставки',endpoint:mp+'/supplier-destinations',fields:async()=>f(fld('account_id','API-аккаунт','select',true,{options:await options('accounts','label')}),fld('node_id','Склад / ПВЗ','select',true,{options:await options('nodes','name','city')}),fld('external_address_id','ID адреса у поставщика','text',false),fld('external_delivery_id','ID способа доставки','text',false),fld('external_requisite_id','ID реквизита (пусто — автоматический выбор, если организация одна)','text',false))},
 'new-used':{title:'Добавить б/у запчасть',endpoint:wb+'/used',fields:async()=>f(fld('store_id','Магазин','select',true,{options:await options('stores')}),fld('original_product_id','Связанный товар','product',false),fld('title','Название б/у детали'),fld('condition','Состояние','select',true,{options:[['used_good','Хорошее'],['used_fair','Удовлетворительное'],['damaged','С дефектами'],['for_parts','На запчасти']]}),fld('mileage_km','Пробег (если известен), км','number',false),fld('description','Описание','textarea',false),fld('defects','Известные дефекты','textarea',false))},
 'new-offer':{title:'Предложение со своего склада',endpoint:wb+'/offers',fields:async()=>f(fld('store_id','Магазин','select',true,{options:await options('stores')}),fld('product_id','Товар из каталога','product'),fld('sku','Внутренний SKU'),fld('price','Цена, ₽','number'))}
};
function formField(def){const name=esc(def.name), req=def.required?'required':'';let element='';if(def.type==='product')element=`<input type="search" data-product-search="${name}" placeholder="Введите артикул или бренд" autocomplete="off"><input type="hidden" name="${name}" ${req}><div data-suggestions="${name}" style="margin-top:5px"></div>`;else if(def.type==='select')element=`<select name="${name}" ${req}><option value="">Выберите…</option>${(def.options||[]).map(([value,label])=>`<option value="${esc(value)}" ${value===def.value?'selected':''}>${esc(label)}</option>`).join('')}</select>`;else if(def.type==='textarea')element=`<textarea name="${name}" rows="3">${esc(def.value||'')}</textarea>`;else if(def.type==='checkbox')element=`<input type="checkbox" name="${name}" ${def.value?'checked':''}>`;else element=`<input name="${name}" type="${def.type}" value="${esc(def.value||'')}" ${def.type==='number'?'min="0" step="any"':''} ${req}>`;return `<label class="${def.type==='textarea'?'full':''}">${esc(def.label)}${element}</label>`}
async function modal(title,desc,fields,submit){$('#modal-save').style.display='';$('#modal-title').textContent=title;$('#modal-desc').textContent=desc;$('#modal-desc').style.color='';$('#modal-fields').innerHTML=fields.map(formField).join('');$$('[data-product-search]',$('#modal-form')).forEach(box=>{let counter=0;box.addEventListener('input',async()=>{const seq=++counter;const q=box.value.trim();const target=$(`[data-suggestions="${box.dataset.productSearch}"]`,$('#modal-form'));$(`[name="${box.dataset.productSearch}"]`,$('#modal-form')).value='';if(q.length<2){target.innerHTML='';return;}try{const data=await api(wb+'/catalog?q='+encodeURIComponent(q)+'&size=12');if(seq!==counter)return;target.innerHTML=data.items.map(p=>`<button type="button" class="mini-action product-choice" data-id="${esc(p.id)}" data-label="${esc(p.brand+' · '+p.article+' · '+p.name)}">${esc(p.brand+' · '+p.article+' · '+p.name)}</button>`).join('')||'<span class="sub">Ничего не найдено</span>';$$('.product-choice',target).forEach(b=>b.onclick=()=>{box.value=b.dataset.label;$(`[name="${box.dataset.productSearch}"]`,$('#modal-form')).value=b.dataset.id;target.innerHTML='';});}catch(e){target.innerHTML=`<span class="sub">${esc(e.message)}</span>`}})});modalHandler=async()=>{const obj={};fields.forEach(field=>{const el=$(`[name="${field.name}"]`,$('#modal-form'));let val=field.type==='checkbox'?el.checked:el.value.trim();if(field.type==='number')val=val===''?undefined:Number(val);if(val!==''&&val!==undefined)obj[field.name]=val;else if(field.name==='receiving_contact_comment')obj[field.name]='';else if(['external_address_id','external_delivery_id','external_requisite_id'].includes(field.name))obj[field.name]=null});await submit(obj);$('#modal').close();toast('Изменения сохранены');load()};$('#modal').showModal()}
async function newForm(action){const cfg=specs[action];if(!cfg)return;const fields=await cfg.fields();await modal(cfg.title,'Заполните поля. Данные будут проверены сервером.',fields,body=>api(cfg.endpoint,{method:'POST',body:JSON.stringify(body)}))}
async function chooseRow(action,id){const name=action;if(name==='edit-offer'){const r=(remember.offers||[]).find(x=>x.id===id);if(!r)return;return modal('Изменить предложение',r.sku,[fld('price','Розничная цена, ₽','number',true,{value:r.price}),fld('status','Статус','select',true,{options:['draft','active','paused','archived'].map(x=>[x,x]),value:r.status})],b=>api(wb+'/offers/'+id,{method:'PATCH',body:JSON.stringify(b)}))}
 if(name==='edit-stock'){const [offerId,preferredNode]=id.split('|');const [offers,nodes,balances]=await Promise.all([api(wb+'/offers'),api(wb+'/nodes'),api(wb+'/inventory')]);const r=offers.find(x=>x.id===offerId);if(!r)return;const eligible=nodes.filter(n=>n.merchant_id===r.merchant_id&&n.kind!=='pickup'&&n.active);const selected=eligible.some(n=>n.id===preferredNode)?preferredNode:eligible[0]?.id;if(!selected)throw Error('Нет доступного склада этого продавца');const current=nodeId=>balances.find(x=>x.offer_id===offerId&&x.node_id===nodeId)?.on_hand??0;await modal('Изменить остаток',r.sku,[fld('node_id','Склад','select',true,{options:eligible.map(n=>[n.id,n.city+' · '+n.name]),value:selected}),fld('on_hand','Физический остаток','number',true,{value:String(current(selected))})],b=>api(wb+'/offers/'+offerId+'/stock',{method:'PUT',body:JSON.stringify(b)}));$('[name="node_id"]',$('#modal-form')).addEventListener('change',e=>{$('[name="on_hand"]',$('#modal-form')).value=String(current(e.target.value))})}
 if(name==='edit-account'){const r=(remember.accounts||[]).find(x=>x.id===id);if(!r)return;return modal('Настроить API-аккаунт',r.label,[fld('label','Название аккаунта','text',true,{value:r.label}),fld('credential_profile','Профиль переменных окружения (не ключ)','text',true,{value:r.credential_profile}),fld('requests_per_hour','Лимит запросов в час','number',true,{value:r.requests_per_hour}),fld('timeout_seconds','Тайм-аут, сек','number',true,{value:r.timeout_seconds}),fld('enabled','Аккаунт включён','checkbox',false,{value:r.enabled}),fld('read_enabled','Разрешить поиск','checkbox',false,{value:r.read_enabled})],b=>api(mp+'/supplier-accounts/'+id,{method:'PATCH',body:JSON.stringify(b)}))}
 if(name==='edit-destination'){const r=(remember.destinations||[]).find(x=>x.id===id);if(!r)return;const [nodes,accounts]=await Promise.all([api(wb+'/nodes'),api(wb+'/accounts')]);const acc=accounts.find(x=>x.id===r.account_id);const valid=nodes.filter(n=>n.merchant_id===acc?.merchant_id&&n.active);return modal('Изменить адрес поставки',r.account+' → '+r.node+' · Изменение адреса, способа доставки или склада сбрасывает подтверждение',[fld('node_id','Наш склад / ПВЗ','select',true,{options:valid.map(n=>[n.id,n.city+' · '+n.name]),value:r.node_id}),fld('external_address_id','ID адреса у поставщика','text',false,{value:r.external_address_id||''}),fld('external_delivery_id','ID способа доставки','text',false,{value:r.external_delivery_id||''}),fld('external_requisite_id','ID организации/реквизита (пусто — авто через API)','text',false,{value:r.external_requisite_id||''}),fld('verified','Адрес проверен в API/договоре','checkbox',false,{value:r.verified}),fld('enabled','Разрешить использовать','checkbox',false,{value:r.enabled})],b=>api(mp+'/supplier-destinations/'+id,{method:'PATCH',body:JSON.stringify(b)}))}
 if(name==='edit-route'){const rows=await api(wb+'/routes');const r=rows.find(x=>x.id===id);if(!r)return;return modal('Параметры маршрута',r.source+' → '+r.target,[fld('transit_days','Доставка, дней','number',true,{value:r.transit_days}),fld('handling_days','Обработка, дней','number',true,{value:r.handling_days}),fld('fee','Тариф до ПВЗ, ₽','number',true,{value:r.fee}),fld('active','Маршрут активен','checkbox',false,{value:r.active})],b=>api(mp+'/routes/'+id,{method:'PATCH',body:JSON.stringify(b)}))}
 if(name==='edit-node'){const rows=await api(wb+'/nodes');const r=rows.find(x=>x.id===id);if(!r)return;const rc=await api(mp+'/nodes/'+id+'/receiving-contact');return modal('Редактировать склад / ПВЗ',r.code+' · Контакт используется при закупке у поставщиков; данные покупателя не подставляются. Изменение адреса или города сбросит подтверждение привязок',[fld('name','Название','text',true,{value:r.name}),fld('city','Город','text',true,{value:r.city}),fld('address','Адрес','text',true,{value:r.address}),fld('timezone_name','Часовой пояс','text',true,{value:r.timezone}),fld('receiving_contact_name','ФИО получателя поставок','text',false,{value:rc.receiving_contact_name||''}),fld('receiving_contact_phone','Телефон получателя поставок','text',false,{value:rc.receiving_contact_phone||''}),fld('receiving_contact_comment','Комментарий contact (может быть пустым)','textarea',false,{value:rc.receiving_contact_comment||''}),fld('active','Точка активна','checkbox',false,{value:r.active})],b=>api(mp+'/nodes/'+id,{method:'PATCH',body:JSON.stringify(b)}))}
 if(name==='edit-used'){const r=(remember.used||[]).find(x=>x.id===id);if(!r)return;return modal('Статус б/у запчасти',r.title,[fld('status','Статус','select',true,{options:['draft','review','published','archived'].map(x=>[x,x]),value:r.status})],b=>api(mp+'/used-parts/'+id,{method:'PATCH',body:JSON.stringify(b)}))}

}
// Order details use readonly modal slot, never display raw JSON or allow changes.
// Extend safe modal to show a readonly summary from trusted table generators.
function renderReadOnly(html,title,desc){$('#modal-title').textContent=title;$('#modal-desc').textContent=desc;$('#modal-desc').style.color='';$('#modal-fields').innerHTML=`<div style="grid-column:1/-1;max-height:62vh;overflow:auto">${html}</div>`;$('#modal-save').style.display='none';modalHandler=null;$('#modal').showModal()}
async function showOrder(id){
 const [order,history]=await Promise.all([api(op+'/orders/'+id),api(mon+'/orders/'+id+'/events')]);
 const lines=table(order.items||[],[['Товар',t('title')],['Количество',t('quantity')],['Цена',r=>money(r.retail)],['Статус',b('status')],['Поставщик',t('supplier')]]);
 const payments=table(order.payments||[],[['Платёж',r=>esc(r.id.slice(0,12))],['Сумма',r=>money(r.amount)],['Статус',b('status')]]);
 const allowed=order.workflow==='v017'&&['request_awaiting_delivery_quote','request_awaiting_operator_review','request_quote_offered','request_quote_declined'].includes(order.status);
 const quote=order.operator_quote?`<p>Котировка № ${esc(order.operator_quote.revision)}, сумма ${money(order.operator_quote.total)}, до ${esc(order.operator_quote.expires_at)}. Оплата недоступна.</p>`:'';
 const action=allowed?`<button type="button" class="mini-action" data-action="quote-order" data-id="${esc(id)}">Направить покупателю предложение цены (без оплаты)</button>`:'';
 const events=table(history.events||[],[['Когда',r=>esc(r.created_at.slice(0,19).replace('T',' '))],['Событие',t('type')],['Источник',t('actor')],['Строка',r=>r.line_id?esc(r.line_id.slice(0,8))+'…':'—']],'События ещё не зарегистрированы');
 renderReadOnly(`${quote}${action}<h3>Позиции</h3>${lines}<h3>Платежи</h3>${payments}<h3>История переходов</h3>${events}<p class="sub">Свободный текст доказательств скрыт; показаны только безопасные метаданные.</p>`,'Заказ '+id.slice(0,12),`${order.total_is_final?'Итого':'Предварительная сумма'} ${money(order.total)} · ${order.status}`);
}
async function quoteOrder(id){
 const order=await api(op+'/orders/'+id);
 if(order.workflow!=='v017'||!order.items?.length)throw Error('Это не предварительная заявка покупателя');
 const fields=[fld('operator_reference','Номер подтверждённого расчёта'),
               fld('valid_for_minutes','Действует минут (5–120)','number',true,{value:'30'})];
 order.items.forEach((item,i)=>{
   fields.push(fld('price_'+i,`Позиция ${i+1}: ${item.title}, цена за единицу ₽`,'number',true,{value:String(item.retail)}));
   fields.push(fld('delivery_'+i,`Позиция ${i+1}: согласованная доставка ₽`,'number',true,{value:'0'}));
   fields.push(fld('evidence_'+i,`Позиция ${i+1}: подтверждение цены и доставки`));
 });
 $('#modal').close();
 await modal('Предложить цену · '+id.slice(0,8),
  'Предложение не списывает деньги и не резервирует товар. Укажите проверенный источник цены, включая доставку.',
  fields,b=>api('/api/v1/admin/marketplace/order-requests/'+id+'/quote',{
    method:'POST',body:JSON.stringify({
      operator_reference:b.operator_reference,valid_for_minutes:b.valid_for_minutes,
      lines:order.items.map((item,i)=>({line_id:item.id,unit_price:b['price_'+i],
        delivery_fee:b['delivery_'+i],cost_evidence:b['evidence_'+i]}))})}));
} 
async function action(name,id){try{
 if(name.startsWith('sf-'))return await sfAction(name,id);
 if(name.startsWith('auto-'))return await autoAction(name,id);
 if(name.startsWith('a2-'))return await a2Action(name);
 if(name.startsWith('f5-'))return await financeTaxRegistersAction(name,id);
 if(name.startsWith('f4-'))return await financePaymentsAction(name,id);
 if(name.startsWith('f3-'))return await financeLedgerAction(name,id);
 if(name.startsWith('f2-'))return await financePricingAction(name,id);
 if(name.startsWith('fin-'))return await financeAction(name,id);
 if(catalogEditor && await catalogEditor.action(name,id))return;
 if(specs[name])return await newForm(name);
 if(name==='monitor-node')return setPage('nodes');if(name==='monitor-dest')return setPage('destinations');
 if(name==='orders-filter'){filters.orderReview=false;filters.orderStatus=$('#orders-status')?.value?.trim()||'';page=1;return load()}
 if(name==='orders-review'){filters.orderReview=true;filters.orderStatus='';page=1;return load()}
 if(name==='orders-all'){filters.orderReview=false;filters.orderStatus='';page=1;return load()}
 if(name==='jobs-filter'){filters.jobStatus=$('#jobs-status')?.value?.trim()||'';page=1;return load()}
 if(name==='jobs-all'){filters.jobStatus='';page=1;return load()}
 if(name==='purchases-filter'){filters.purchaseStatus=$('#purchase-status')?.value?.trim()||'';page=1;return load()}
 if(name==='purchases-all'){filters.purchaseStatus='';page=1;return load()}
 if(name==='retry')return load();if(name==='prev'){page=Math.max(1,page-1);return load()}if(name==='next'){page++;return load()}
 if(name==='search'){search=($('#q')?.value||'').trim();filters.status=$('#status-filter')?.value||'';page=1;return load()}
 if(name==='local-search'){search=$('#local-filter')?.value||'';return load()}
 if(name==='publish'){if(!confirm('Опубликовать товар в общем каталоге?'))return;await api(bo+'/products/'+id+'/publish',{method:'POST'});toast('Товар опубликован');return load()}
 if(name==='supplier')return supplierDetails(id);if(name==='back-suppliers')return setPage('suppliers');
 if(name==='save-supplier'){const body={};$$('[data-setting]').forEach(el=>body[el.dataset.setting]=el.type==='checkbox'?el.checked:Number(el.value));await api(en+'/suppliers/'+encodeURIComponent(id)+'/configuration',{method:'PATCH',body:JSON.stringify(body)});toast('Политика сохранена');return supplierDetails(id)}
 if(name==='supplier-refresh'){const productId=prompt('UUID опубликованного товара для обновления у '+id+':');if(!productId)return;if(!/^[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12}$/i.test(productId))throw Error('Некорректный UUID товара');await api(en+'/products/'+productId+'/refresh?code='+encodeURIComponent(id),{method:'POST'});return toast('Запрос на обновление обработан (проверьте очередь и лимиты)')}
 if(name==='probe-account'){
   if(!confirm('Выполнить один read-only вызов ROSSKO GetCheckoutDetails? Он расходует квоту API.'))return;
   const data=await api(mp+'/supplier-accounts/'+id+'/checkout-details',{method:'POST'});
   remember.rosskoProbe=data;
   const addresses=table(data.addresses||[],[['ID',t('id')],['Город',t('city')],['Улица',t('street')],['Дом',t('house')],['Разрешённые способы',r=>esc((r.allowed_delivery_ids||[]).join(', '))],['Действие',(r,i)=>act('Привязать к складу','probe-map',String((data.addresses||[]).indexOf(r)))]]);
   const deliveries=table(data.deliveries||[],[['ID',t('id')],['Способ',t('name')]]);
   const payments=table(data.payments||[],[['ID',t('id')],['Оплата',t('name')]]);
   return renderReadOnly('<h3>Адреса API</h3>'+addresses+'<h3>Доставка</h3>'+deliveries+'<h3>Оплата поставщику</h3>'+payments,
     'ROSSKO: доступные адреса','Выберите адрес и нажмите «Привязать к складу». Новый маршрут по умолчанию не подтверждён и отключён.');
 }
 if(name==='probe-map'){
   const probe=remember.rosskoProbe;const addr=probe?.addresses?.[Number(id)];
   if(!probe||!addr||!addr.id)throw Error('Сначала обновите список адресов ROSSKO');
   const [accounts,nodes,mappings]=await Promise.all([api(wb+'/accounts'),api(wb+'/nodes'),api(wb+'/destinations')]);
   const account=accounts.find(a=>a.id===probe.account_id);if(!account)throw Error('API-аккаунт не найден');
   const eligible=nodes.filter(n=>n.merchant_id===account.merchant_id&&n.active);
   if(!eligible.length)throw Error('Создайте склад или ПВЗ для этого продавца');
   const ids=(addr.allowed_delivery_ids||[]).map(String);
   const deliveries=(probe.deliveries||[]).filter(d=>String(d.id)==='000000002'&&(!ids.length||ids.includes(String(d.id))));
   if(!deliveries.length)throw Error('ROSSKO не вернул подходящего способа доставки для адреса');
   const selected=mappings.filter(m=>m.account_id===probe.account_id);
   const closed=new Promise(resolve=>$('#modal').addEventListener('close',resolve,{once:true}));
   $('#modal').close();await closed;
   const fields=[fld('node_id','Наш склад / ПВЗ','select',true,{options:eligible.map(n=>[n.id,n.city+' · '+n.name])}),fld('external_delivery_id','Способ доставки ROSSKO','select',true,{options:deliveries.map(d=>[d.id,d.name+' · '+d.id])})];
   await modal('Привязать адрес ROSSKO',addr.city+' · '+addr.street+' '+addr.house+' (ID '+addr.id+')',fields,async b=>{
     const existing=selected.find(m=>m.node_id===b.node_id);
     if(existing){
       await api(mp+'/supplier-destinations/'+existing.id,{method:'PATCH',body:JSON.stringify({external_address_id:addr.id,external_delivery_id:b.external_delivery_id,verified:false,enabled:false})});
     }else{
       await api(mp+'/supplier-destinations',{method:'POST',body:JSON.stringify({account_id:probe.account_id,node_id:b.node_id,external_address_id:addr.id,external_delivery_id:b.external_delivery_id})});
     }
     setPage('destinations');
   });
   return;
 }
 if(name==='order')return showOrder(id);
 if(name==='quote-order')return quoteOrder(id);
 if(name.startsWith('edit-')){if(name==='edit-offer'||name==='edit-stock'||name==='edit-account'||name==='edit-destination'||name==='edit-used'||name==='edit-route'||name==='edit-node')return chooseRow(name,id)}
 }catch(e){toast(e.message,true)}}
function logout(){catalogEditor?.resetPreview();token='';$('#workspace').classList.add('hidden');$('#login').classList.remove('hidden');$('#token').value='';$('#auth-error').textContent=''}
if(window.createCatalogEditor){catalogEditor=window.createCatalogEditor({api,apiBlob,modal,fld,toast,render,surface,table,esc,act,headerAction,setPage,load});Object.assign(pages,catalogEditor.pages);}
$('#result').addEventListener('submit',async e=>{if(e.target.id==='sf-upload'){e.preventDefault();try{await supplierFeedUpload(e.target)}catch(err){toast(err.message,true)}return;}if(e.target.id==='sf-price'){e.preventDefault();try{const scopes=(await api(sfEndpoint+'/pricing/scopes')).items||[];const scope=scopes[Number($('#sf-price-scope').value)];if(!scope)throw Error('Выберите подтверждённый склад и аккаунт');const data=Object.fromEntries(new FormData(e.target).entries());await api(sfEndpoint+'/pricing/policies',{method:'PUT',body:JSON.stringify({...scope,...data})});toast('Проект тарифа сохранён. Требуется отдельное утверждение.');await load()}catch(err){toast(err.message,true)}return;}if(e.target.id==='ce-upload')catalogEditor?.upload(e)});
$('#result').addEventListener('change',e=>{if(e.target.id==='sf-supplier'){const el=$('#sf-file');if(el){el.accept=e.target.value==='rossko'?'.zip,.xlsx':'.csv';el.value=''}}});
$('#login-form').addEventListener('submit',async e=>{e.preventDefault();const value=$('#token').value.trim();$('#auth-error').textContent='Проверяем доступ…';token=value;$('#token').value='';try{await api(wb+'/summary');$('#login').classList.add('hidden');$('#workspace').classList.remove('hidden');setPage((location.hash||'#dashboard').slice(1) in pages?location.hash.slice(1):'dashboard');$('#auth-error').textContent=''}catch(e){token='';$('#auth-error').textContent=e.message}});
$('#toggle-token').onclick=()=>{$('#token').type=$('#token').type==='password'?'text':'password'};
$('#logout').onclick=logout;$('#refresh').onclick=()=>load();$('#menu-button').onclick=()=>{$('#sidebar').classList.add('open');$('#mobile-dimmer').classList.add('open')};$('#mobile-dimmer').onclick=()=>{$('#sidebar').classList.remove('open');$('#mobile-dimmer').classList.remove('open')};
$('#nav').addEventListener('click',e=>{const b=e.target.closest('[data-view]');if(b)setPage(b.dataset.view)});
$('#result').addEventListener('click',e=>{const b=e.target.closest('[data-view],[data-action]');if(!b)return;if(b.dataset.view)setPage(b.dataset.view);else action(b.dataset.action,b.dataset.id)});
$('#modal-fields').addEventListener('click',e=>{const b=e.target.closest('[data-action="probe-map"]');if(b)action(b.dataset.action,b.dataset.id)});
$('#heading-actions').addEventListener('click',e=>{const b=e.target.closest('[data-action]');if(b)action(b.dataset.action,b.dataset.id)});
$('#modal-close').onclick=()=>$('#modal').close();$('#modal-cancel').onclick=()=>$('#modal').close();$('#modal').addEventListener('close',()=>{$('#modal-save').style.display='';modalHandler=null});
$('#modal-form').addEventListener('submit',async e=>{e.preventDefault();if(!modalHandler)return;const btn=$('#modal-save');btn.disabled=true;try{await modalHandler()}catch(err){$('#modal-desc').textContent='Ошибка: '+err.message;$('#modal-desc').style.color='#b43b4c'}finally{btn.disabled=false}});
})();
