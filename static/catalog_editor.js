/* v0.23 Product Workspace: three-column operator experience, scoped to existing token shell. */
window.createCatalogEditor = function(ctx) {
  'use strict';
  const {api, apiBlob, modal, fld, toast, render, surface, table, esc, act, headerAction, setPage, load} = ctx;
  const C='/api/v1/backoffice/catalog-v021', E='/api/v1/backoffice/catalog-editor';
  const M='/api/v1/backoffice/master-catalog', B='/api/v1/backoffice', W='/api/v1/backoffice/workspace';
  const state={brand:'',category:'',attribute:'',product:'',family:'',q:'',p:1,previewUrls:[],pwTheme:(()=>{try{return localStorage.getItem('autocodex-operator-theme')==='dark'?'dark':'light'}catch(e){return 'light'}})(),pwDirty:false};
  const sel=(name,choices,value='',allowEmpty=true)=>fld(name,name,'select',!allowEmpty,{options:choices.map(x=>[x.id,x.name||x.label||x.code]),value});
  const options=(rows,empty='Не выбрано')=>`<option value="">${esc(empty)}</option>`+rows.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
  const btn=(label,name,id='')=>act(label,'ce-'+name,id);
  const section=(title,html,sub='')=>surface(title,html,sub);
  function resetPreview(){for(const url of state.previewUrls)URL.revokeObjectURL(url);state.previewUrls=[];}
  async function previews(){resetPreview();for(const el of document.querySelectorAll('[data-ce-media]')){
    try {const blob=await apiBlob(`${E}/media/${el.dataset.ceMedia}/preview?variant=thumb`);
      if(!el.isConnected)continue;const url=URL.createObjectURL(blob);state.previewUrls.push(url);el.src=url;
    }catch(e){if(el.isConnected)el.replaceWith(Object.assign(document.createElement('span'),{textContent:'Предпросмотр недоступен'}));}
  }}
  function familyLabel(x){return `${x.name} (${x.kind}; ${x.axes.join(', ')})`;}
  async function listBrands(){
    headerAction('Добавить бренд','new-brand');const d=await api(C+`/brands?limit=100&offset=${(state.p-1)*100}&q=${encodeURIComponent(state.q)}`);
    const rows=table(d.items,[['Производитель',r=>`<b>${esc(r.name)}</b><div class="sub">${esc(r.slug)}</div>`],['Действия',r=>btn('Профиль и псевдонимы','brand',r.id)]],'Брендов нет');
    render('',section('Бренды',`<div class="ce-filters"><input class="ce-search" id="ce-q" placeholder="Название бренда" value="${esc(state.q)}"><button class="secondary-button" data-action="ce-search-brands">Поиск</button></div>${rows}${pagination(d.items.length,100)}`,'Используется единая идентичность производителя; псевдонимы подтверждаются вручную'));
  }
  function pagination(count,size){return `<div class="ce-pager"><button class="secondary-button" ${state.p===1?'disabled':''} data-action="ce-prev">← Ранее</button><span>Страница ${state.p}</span><button class="secondary-button" ${count<size?'disabled':''} data-action="ce-next">Далее →</button></div>`;}
  async function brandDetail(){
    if(!state.brand)return listBrands();
    const [brand,alias,media]=await Promise.all([api(`${E}/brands/${state.brand}/profile`),api(`${C}/brands/${state.brand}/aliases`),api(`${E}/brands/${state.brand}/media`)]);
    headerAction('Добавить псевдоним','ce-new-alias');
    const aliases=table(alias.items,[['Псевдоним',r=>esc(r.alias)],['Область',r=>esc(r.supplier_id?'Поставщик':'Все поставщики')],['Статус',r=>r.verified?'Подтверждён':'На проверке'],['Действия',r=>btn(r.verified?'Отозвать':'Подтвердить','verify-alias',r.id)+' '+btn('Удалить','delete-alias',r.id)]],'Псевдонимов нет');
    const profile=brand.profile||{};
    const mediaHtml=mediaList(media.items);
    render('',`<div class="ce-tools">${btn('← Бренды','back-brands')}${btn('Изменить название','rename-brand')}${btn('Редактировать профиль','edit-brand-profile')}</div>`+
      section(brand.name,`<div class="ce-kv"><span>UUID</span><code>${esc(brand.id)}</code><span>Slug</span><span>${esc(brand.slug)}</span><span>Юридическое имя</span><span>${esc(profile.legal_name||'—')}</span><span>Сайт</span><span>${esc(profile.website||'—')}</span><span>Страна</span><span>${esc(profile.country_code||'—')}</span><span>Проверено</span><span>${profile.verified?'Да':'Нет'}</span></div><p>${esc(profile.description||'')}</p>`)+
      section('Альтернативные наименования',aliases,'Конфликты с официальными брендами запрещены')+
      section('Логотипы',mediaHtml+uploadForm('brand',brand.id),'Черновики и отклонённые файлы доступны только администратору'));
    await previews();
  }
  async function listCategories(){
    const d=await api(C+'/categories');headerAction('Создать категорию','ce-new-category');
    const nodes=new Map(d.items.map(x=>[x.id,x]));const depth=(row)=>{let result=0,id=row.parent_id,seen=new Set([row.id]);while(id&&nodes.has(id)&&!seen.has(id)&&result<30){seen.add(id);result++;id=nodes.get(id).parent_id;}return result};
    const sorted=[];const seen=new Set();
    function append(parent){const children=d.items.filter(x=>x.parent_id===parent).sort((a,b)=>a.position-b.position||a.name.localeCompare(b.name,'ru'));
      for(const c of children){if(seen.has(c.id))continue;seen.add(c.id);sorted.push(c);append(c.id);}}
    append(null);for(const c of d.items){if(!seen.has(c.id)){seen.add(c.id);sorted.push(c);append(c.id)}};
    const rows=table(sorted,[['Категория',r=>`<span style="padding-left:${depth(r)*16}px">${depth(r)?'↳ ':''}<b>${esc(r.name)}</b></span><div class="sub">${esc(r.slug)}</div>`],['Порядок',r=>esc(r.position)],['Статус',r=>r.active?'Активна':'Выключена'],['Действие',r=>btn('Редактировать','category',r.id)]],'Категорий нет');
    render('',section('Дерево категорий',rows,'Вложенность и проверки циклов обеспечиваются сервером'));
  }
  async function categoryDetail(){
    if(!state.category)return listCategories();
    const [all,bind]=await Promise.all([api(C+'/categories'),api(`${E}/categories/${state.category}/bindings`)]);
    const cat=all.items.find(x=>x.id===state.category);if(!cat)throw Error('Категория не найдена');
    headerAction('Создать характеристику','ce-create-category-attribute');
    const rows=table(bind.items,[['Атрибут',r=>`<b>${esc(r.name)}</b><span class="sub"> · ${esc(r.code)}</span>`],['Источник',r=>r.direct?'Эта категория':'Наследуется'],['Группа',r=>esc(r.group_name||'Основные')],['Порядок',r=>esc(r.sort_order||0)],['Обязат.',r=>r.required?'Да':'Нет'],['Фильтр',r=>r.show_in_filters?'Да':'Нет'],['Действия',r=>btn('Настроить','template-binding',r.attribute_id)+' '+btn('Изменить поле','attribute',r.attribute_id)+' '+(r.direct?btn('Отвязать','unbind-attribute',r.attribute_id):'' )]],'Нет характеристик');
    render('',`<div class="ce-tools">${btn('← Категории','back-categories')}${btn('Редактировать категорию','edit-category')}${btn(cat.active?'Выключить':'Включить','toggle-category')}</div>`+
      section(cat.name,`<div class="ce-kv"><span>UUID</span><code>${esc(cat.id)}</code><span>Slug</span>${esc(cat.slug)}<span>Родительская</span>${esc(all.items.find(x=>x.id===cat.parent_id)?.name||'Корневая')}<span>Порядок</span>${esc(cat.position)}</div>`)+
      section('Шаблон характеристик и фильтры',`<div class="ce-tools">${btn('Привязать существующую','bind-attribute')} ${btn('Создать новую','create-category-attribute')}</div>${rows}`,'Характеристики наследуются от родителей. Можно переопределить порядок, группу и видимость фильтра в дочерней категории; значения товаров не удаляются.'));
  }
  async function listAttributes(){
    headerAction('Создать характеристику','ce-new-attribute');
    const d=await api(C+'/attributes');
    render('',section('Характеристики',table(d.items,[['Код',r=>`<b>${esc(r.code)}</b>`],['Название',r=>esc(r.name)],['Тип',r=>esc(r.data_type)],['Ед.',r=>esc(r.unit||'—')],['Фильтр',r=>r.filterable?'Да':'Нет'],['Состояние',r=>r.active?'Активна':'Выключена'],['Действия',r=>btn('Редактировать','attribute',r.id)+' '+btn('Удалить','delete-attribute',r.id)]],'Характеристик нет'),'Код и тип редактируются с проверкой использования и безопасным переносом значений. UUID неизменяем.'));
  }
  async function listFamilies(){
    headerAction('Создать семейство','ce-new-family');const d=await api(E+`/families?limit=50&offset=${(state.p-1)*50}&q=${encodeURIComponent(state.q)}`);
    render('',section('Семейства товаров',`<div class="ce-filters"><input id="ce-q" value="${esc(state.q)}" placeholder="Поиск по семействам"><button class="secondary-button" data-action="ce-search-families">Поиск</button></div>`+
      table(d.items,[['Семейство',r=>`<b>${esc(r.name)}</b><div class="sub">${esc(r.slug)}</div>`],['Тип',r=>esc(r.kind)],['Оси вариантов',r=>esc(r.axes.join(', '))],['Действие',r=>btn('Участники','family',r.id)]],'Семейств нет')+pagination(d.items.length,50),`Всего ${d.total}; цены и остатки каждого товара самостоятельны`));
  }
  async function familyDetail(){
    if(!state.family)return listFamilies();const d=await api(E+'/families/'+state.family);
    headerAction('Добавить товар','ce-new-member');
    render('',`<div class="ce-tools">${btn('← Семейства','back-families')}</div>`+section(d.name,`<p>Тип: ${esc(d.kind)} · Оси: ${esc(d.axes.join(', '))}</p>`+
      table(d.members,[['Товар',r=>esc(r.name)],['Значения варианта',r=>esc(Object.values(r.axis_values).join(' / '))],['Действия',r=>btn('Карточка','product',r.product_id)+' '+btn('Убрать из семейства','remove-member',r.product_id)]],'В семействе пока нет вариантов')));
  }
  function mediaList(items){return `<div class="ce-media-list">${items.map(r=>`<div class="ce-media-item">${r.kind==='document'?'<span class="ce-document">PDF</span>':`<img data-ce-media="${esc(r.id)}" alt="${esc(r.alt_text||r.filename)}">`}<div><b>${esc(r.filename)}</b><div class="sub">${esc(r.kind)} · ${esc(r.status)} · позиция ${r.position}</div><div class="ce-tools">${btn('Изменить','media-edit',r.id)} ${btn('Открыть','media-download',r.id)}</div></div></div>`).join('')||'<p class="sub">Медиафайлы отсутствуют</p>'}</div>`;}
  function uploadForm(kind,id){return `<form class="ce-upload" id="ce-upload" data-owner="${esc(kind)}" data-id="${esc(id)}"><label>Файл JPEG, PNG, WebP или PDF<input type="file" name="upload" accept="image/jpeg,image/png,image/webp,application/pdf" required></label><label>Тип<select name="kind">${(kind==='brand'?[['logo','Логотип']]:[['image','Фото'],['document','Документ']]).map(x=>`<option value="${x[0]}">${x[1]}</option>`).join('')}</select></label><label>Описание<input name="alt_text" maxlength="250"></label><label>Порядок<input name="position" type="number" min="0" max="100000" value="0"></label><button type="submit" class="primary">Загрузить</button></form>`;}
  function pwPanel(title,html,sub='',open=true,cls=''){
    return `<details class="pw-panel ${cls}" ${open?'open':''}><summary><strong>${esc(title)}</strong><span class="pw-fold" aria-hidden="true">⌄</span></summary><div class="pw-panel-body">${sub?`<p class="pw-help">${esc(sub)}</p>`:''}${html}</div></details>`;
  }
  function pwField(name,label,value,opts={}){
    const typ=opts.area?'textarea':'input';const extra=opts.area?`rows="${opts.rows||3}"`:`type="${opts.type||'text'}"`;
    const content=opts.area?esc(value||''):'';
    return `<label class="pw-field ${opts.wide?'pw-wide':''}"><span>${esc(label)}</span><${typ} name="${name}" ${extra} ${opts.max?`maxlength="${opts.max}"`:''} ${opts.required?'required':''} ${opts.readonly?'readonly':''} ${typ==='input'?`value="${esc(value??'')}"`:''}>${content}</${typ}></label>`;
  }
  function pwSummary(label,value){return `<div class="pw-stat"><span>${esc(label)}</span><b>${esc(value??'—')}</b></div>`;}
  async function productDetail(){
    if(!state.product)return listProducts();
    const pid=state.product;
    const [p,categories,attrdefs,links,media,offers,history,changeHistory,attributeForm,readiness]=await Promise.all([
      api(`${E}/products/${pid}/identity`),api(C+'/categories'),api(C+'/attributes'),
      api(`${M}/products/${pid}/links`),api(`${E}/media?product_id=${pid}`),
      api(`${E}/products/${pid}/sale-terms`),api(`${E}/products/${pid}/slug-history`),api(`${E}/products/${pid}/change-history`),api(`${E}/products/${pid}/attribute-form`),api(`${E}/products/${pid}/readiness`)
    ]);
    const cats=p.categories||[], family=links.family, comp=links.composition;
    const catRows=table(cats,[['Категория',r=>esc(categories.items.find(x=>x.id===r.category_id)?.name||r.category_id)],['Роль',r=>r.is_primary?'Основная':'Доп.'],['',r=>btn('Убрать','remove-product-category',r.category_id)]],'Нет категорий');
    const configured=attributeForm.items||[];
    const vRows=table(configured,[['Характеристика',r=>`<b>${esc(r.name)}</b>${r.required?' <span class="pw-required">*</span>':''}<div class="sub">${esc(r.group_name)} · ${esc(r.code)}${r.filterable&&r.show_in_filters?' · фильтр':''}</div>`],['Значение',r=>r.value===null?'<span class="pw-empty">Не заполнено</span>':esc(r.value+(r.unit?' '+r.unit:''))],['',r=>btn('Изменить','set-product-attribute',r.attribute_id)+(r.value!==null?' '+btn('Убрать','remove-product-attribute',r.attribute_id):'')]],'Категория пока не настроена. Откройте «Категории → Шаблон характеристик».');
    const familyHtml=family?`<b>${esc(family.name)}</b> <span class="sub">${esc(family.kind)}</span><div class="pw-chips">${family.members.map(x=>`<span class="ce-pill">${esc(x.name)} (${esc(Object.values(x.axis_values).join(' / '))}) ${btn('Открыть','product',x.product_id)}</span>`).join('')}</div>`:'Семейство пока не назначено';
    const compHtml=`<div class="ce-tools">${btn('Тип и проверка состава','composition')} ${comp?btn('Добавить компонент','new-component'):''}</div>`+
      (comp?`<div class="pw-help">${esc(comp.kind)} · ${comp.verified?'Проверен':'Не проверен'} · ${comp.parts.length} позиций</div>`+table(comp.parts,[['№',r=>esc(r.position)],['Компонент',r=>esc(r.name)],['Количество',r=>esc(r.quantity+' '+r.unit)],['',r=>btn('Убрать','remove-component',r.id)]],'Компонентов нет'):'Состав не задан');
    const relHtml=table(links.relations,[['Товар',r=>esc(r.name)],['Тип',r=>esc(r.relation)],['Статус',r=>r.verified?'Проверена':'Черновик'],['',r=>btn('Открыть','product',r.related_product_id)+' '+btn(r.verified?'Отозвать':'Проверить','verify-relation',r.id)+' '+btn('Удалить','remove-relation',r.id)]],'Связей нет');
    const packHtml=table(links.factory_packages,[['Внешний',r=>esc(r.outer_product_id.slice(0,8))],['Внутренний',r=>esc(r.inner_product_id.slice(0,8))],['Штук',r=>esc(r.units_per_package)],['',r=>btn(r.verified?'Отозвать':'Проверить','verify-package',r.id)+' '+btn('Удалить','remove-package',r.id)]],'Упаковок нет');
    const offerHtml=table(offers.seller_offers,[['Продавец/SKU',r=>esc(r.sku)],['Цена',r=>esc(r.price)],['Статус',r=>esc(r.status)],['Наличие',r=>(r.stock_by_node||[]).map(n=>`<div class="sub">${esc(n.city)} · ${esc(n.node)}: ${esc(n.available)} шт.</div>`).join('')||'Нет остатков'],['Кратность',r=>r.terms?`${esc(r.terms.minimum_units)} / шаг ${esc(r.terms.step_units)}`:'—'],['',r=>btn('Цена / статус','edit-offer',r.offer_id)+' '+btn('Остатки','edit-stock',r.offer_id)+' '+btn('Правила','sale-terms',r.offer_id)]],'Нет предложений');
    const supplierList=(offers.supplier_offer_snapshots||[]), suppliersTotal=supplierList.length;
    const supplierHtml=table(supplierList.slice(0,25),[['Поставщик',r=>`<b>${esc(r.supplier)}</b><div class="sub">${esc(r.raw_article)} · ${esc(r.matching)}</div>`],['Закупка',r=>esc(r.purchase_price+' '+r.currency)],['Остаток/дней',r=>esc((r.quantity??'—')+' / '+(r.lead_time_days??'—'))],['Дата',r=>esc(r.observed_at)]],'Исторических предложений пока нет');
    const editsHtml=changeHistory.items.map(x=>`<div class="pw-redirect"><small>${esc(x.changed_at)} · ${esc(x.action)}</small><span>${esc(Object.keys(x.changes).join(', '))}</span></div>`).join('')||'<p class="sub">Нет изменений в журнале Product Workspace</p>';
    const historyHtml=history.items.map(h=>`<div class="pw-redirect"><code>${esc(h.slug)}</code><span>301 → /product/${esc(p.slug)}</span></div>`).join('')||'<p class="sub">Адрес ещё не менялся</p>';
    const mediaHtml=mediaList(media.items)+uploadForm('product',p.id);
    const publicLink=p.status==='published'?`<a class="secondary-button pw-link" href="/product/${encodeURIComponent(p.slug)}" target="_blank" rel="noopener noreferrer">↗ На витрину</a>`:'<span class="pw-hint">На витрине не опубликован</span>';
    const actions=p.status==='published'?btn('Снять с публикации','unpublish-product'):(p.status==='draft'||p.status==='review'?btn('Опубликовать','publish-product'):'');
    const left=`${pwPanel('Медиа и документы',mediaHtml,'Загрузка не публикует фото автоматически. Проверяйте alt и статус модерации.',true)}`+
      pwPanel('Основная информация',`<div class="pw-readonly">${pwSummary('Бренд',p.brand)}${pwSummary('Артикул',p.article)}${pwSummary('UUID',p.id)}</div><div class="pw-form-grid">${pwField('name','Название товара',p.name,{required:true,max:300,wide:true})}${pwField('description','Описание',p.description,{area:true,rows:5,wide:true,max:10000})}</div>`,'Редактирование полей и SEO — одной атомарной кнопкой «Сохранить».',true)+
      pwPanel('SEO и адрес',`<div class="pw-form-grid">${pwField('slug','Slug — адрес карточки',p.slug,{required:true,max:199,wide:true})}<p class="pw-help pw-wide" id="pw-slug-hint">Старый slug получит прямой 301 на новый URL.</p>${pwField('seo_title','SEO title',p.seo_title||'',{max:250,wide:true})}${pwField('seo_h1','H1',p.seo_h1||'',{max:300,wide:true})}${pwField('seo_description','Meta description',p.seo_description||'',{area:true,rows:3,max:500,wide:true})}<label class="pw-checkbox pw-wide"><input type="checkbox" name="seo_noindex" ${p.seo_noindex?'checked':''}> Исключить карточку из индексирования</label></div><div class="pw-seo-preview"><span>Предпросмотр поиска</span><b id="pw-preview-title">${esc(p.seo_title||p.name)}</b><small id="pw-preview-url">/product/${esc(p.slug)}</small><p id="pw-preview-desc">${esc(p.seo_description||p.description||'')}</p></div>${pwPanel('История slug и 301',historyHtml,'Исторические адреса зарезервированы; цепочек редиректов нет.',false)}`,'Сохранение нового slug не меняет UUID, артикул и связи с поставщиками.',true)+
      pwPanel('Категории',`<div class="ce-tools">${btn('Добавить категорию','assign-category')}</div>${catRows}`,'У товара допускается только одна основная категория.',false);
    const middle=pwPanel('Характеристики по категории',`<div class="ce-tools">${btn('Добавить / изменить / удалить массово','fill-template')} ${btn('Выбрать поле','choose-attribute')} ${attributeForm.primary_category_id?btn('Создать поле','create-product-attribute'):''} ${attributeForm.primary_category_id?btn('Шаблон категории','template-category',attributeForm.primary_category_id):''}</div><p class="pw-help">Настроено: ${configured.length}. Заполнено: ${configured.filter(x=>x.value!==null).length}. Обязательно и не заполнено: ${attributeForm.missing_required.length}.</p>${vRows}`,'Поля, типы, единицы, обязательность и фильтры задаются в категории и наследуются.',true)+
      pwPanel('Семейство и варианты',`<div class="ce-tools">${btn('Привязать к семейству','attach-family')}${family?btn('Открыть семейство','open-family',family.id):''}</div>${familyHtml}`,'У каждого варианта свой UUID, цены и остатки.',true)+
      pwPanel('Комплект и компоненты',compHtml,'Заводской состав — отдельная сущность. Компоненты могут иметь свои Product.',true)+
      pwPanel('Аналоги, замены и связанные',`<div class="ce-tools">${btn('Создать связь','new-relation')}</div>${relHtml}`,'Проверенная совместимость не выводится из названия.',false)+
      pwPanel('Заводские упаковки',`<div class="ce-tools">${btn('Добавить упаковку','new-package')}</div>${packHtml}`,'У внешнего и внутреннего товара разные UUID.',false);
    const right=pwPanel('Собственные и сторонние предложения',`<div class="pw-kpis">${pwSummary('Предложения',offers.seller_offers.length)}${pwSummary('API соответствия',offers.supplier_item_count)}</div>${offerHtml}`,'Цены каждого продавца меняются независимо. Изменения условий — в отдельном диалоге.',true)+
      pwPanel('История цен поставщиков',`<div class="pw-help">${suppliersTotal} снимков в API-проекции, показаны первые ${Math.min(suppliersTotal,25)}.</div>${supplierHtml}`,'Исторические закупочные цены нельзя переписать вручную. Нет запросов к API поставщиков.',false)+
      pwPanel('Готовность товара',`<div class="pw-readonly">${pwSummary('Статус',p.status)}${pwSummary('Основная категория',cats.some(x=>x.is_primary)?'Да':'Нет')}${pwSummary('Характеристики',p.attributes.length)}${pwSummary('Комплект',comp?.kind||'—')}</div>${readiness.issues.map(x=>`<p class="ce-warning"><b>${x.severity==='error'?'Ошибка':'Проверить'}:</b> ${esc(x.message)}</p>`).join('')||'<p class="pw-help">Критических замечаний нет.</p>'}`,'Сохранение и публикация — независимые действия.',false)+
      pwPanel('Журнал изменений',editsHtml,'Показаны 100 последних изменений данных товара, не операций поставщиков.',false);
    document.documentElement.dataset.operatorTheme=state.pwTheme;
    render('',`<div class="pw-workspace" data-pw-theme="${state.pwTheme==='dark'?'dark':'light'}"><div class="pw-toolbar"><div class="pw-toolbar-main"><div class="ce-tools">${btn('← Каталог','back-products')}</div><div><div class="pw-kicker">PRODUCT WORKSPACE · v0.23</div><h2>${esc(p.name)}</h2><div class="pw-subtitle">${esc(p.brand)} · ${esc(p.article)} · <span class="pw-status">${esc(p.status)}</span></div></div></div><div class="pw-actions"><span class="pw-unsaved" id="pw-unsaved" aria-live="polite"></span><button type="button" id="pw-theme" class="secondary-button" aria-label="Сменить тему редактора">◐ Тема</button>${publicLink}${actions}<button class="primary" type="button" id="pw-save" disabled>Сохранить изменения</button></div></div><div id="pw-main-form" class="pw-grid" data-updated="${esc(p.updated_at||'')}"><div class="pw-column"><div class="pw-column-title">01 · Товар и контент</div>${left}</div><div class="pw-column"><div class="pw-column-title">02 · Структура и связи</div>${middle}</div><div class="pw-column"><div class="pw-column-title">03 · Цены и предложения</div>${right}</div></div></div>`);
    // No silent autosave; one request persists the primary product and SEO atomically.
    const form=document.getElementById('pw-main-form'),save=document.getElementById('pw-save');
    state.pwDirty=false;
    const dirty=()=>{state.pwDirty=true;save.disabled=false;document.getElementById('pw-unsaved').textContent='● Есть несохранённые изменения';};
    const updatePreview=()=>{
      const get=k=>form.querySelector(`[name=\"${k}\"]`)?.value||'';
      document.getElementById('pw-preview-title').textContent=get('seo_title')||get('name');
      document.getElementById('pw-preview-url').textContent='/product/'+get('slug');
      document.getElementById('pw-preview-desc').textContent=get('seo_description')||get('description');
    };
    let seq=0;
    form.addEventListener('input',async e=>{
      if(e.target.name&&!e.target.closest('#ce-upload')){dirty();updatePreview();}
      if(e.target.name==='slug'){
        const slug=e.target.value;const hint=document.getElementById('pw-slug-hint');const local=++seq;
        if(!/^[a-z0-9][a-z0-9-]{2,198}$/.test(slug)){hint.textContent='Slug: только строчные латинские буквы, цифры и дефис (3–199 символов).';return;}
        try{const d=await api(`${E}/products/${pid}/slug-availability?slug=${encodeURIComponent(slug)}`);if(local===seq)hint.textContent=d.available?'✓ Slug свободен. При сохранении старый адрес получит 301.':'✕ Slug занят или зарезервирован историей';}
        catch(e){if(local===seq)hint.textContent='Проверка slug: '+e.message;}
      }
    });
    form.addEventListener('change',e=>{if(e.target.name&&!e.target.closest('#ce-upload'))dirty();});
    save.addEventListener('click',async e=>{
      e.preventDefault();if(!state.pwDirty)return;
      const val=name=>form.querySelector(`[name=\"${name}\"]`)?.value||'';
      const body={name:val('name'),description:val('description')||null,slug:val('slug'),
        seo_title:val('seo_title')||null,seo_h1:val('seo_h1')||null,
        seo_description:val('seo_description')||null,
        seo_noindex:form.querySelector('[name=seo_noindex]').checked,
        expected_updated_at:form.dataset.updated};
      save.disabled=true;
      try{await api(`${E}/products/${pid}/workspace`,{method:'PATCH',body:JSON.stringify(body)});
        state.pwDirty=false;toast('Товар и SEO сохранены; история URL обновлена');await load();}
      catch(err){save.disabled=false;toast(err.message,true);}
    });
    document.getElementById('pw-theme').onclick=()=>{state.pwTheme=state.pwTheme==='dark'?'light':'dark';document.querySelector('.pw-workspace').dataset.pwTheme=state.pwTheme;document.documentElement.dataset.operatorTheme=state.pwTheme;try{localStorage.setItem('autocodex-operator-theme',state.pwTheme)}catch(e){}};
    // Warn on navigating away from unsaved product/SEO fields.
    document.querySelectorAll('.pw-workspace [data-action]').forEach(el=>el.addEventListener('click',ev=>{
      if(state.pwDirty&&!confirm('Есть несохранённые поля товара и SEO. Покинуть редактирование?')){ev.preventDefault();ev.stopImmediatePropagation();}
    },true));
    await previews();
  }
  function listProducts(){setPage('catalog');}
  async function openProduct(id){state.product=id;setPage('ce-product');}
  async function openBrand(id){state.brand=id;setPage('ce-brand');}
  async function openCategory(id){state.category=id;setPage('ce-category');}
  async function openFamily(id){state.family=id;setPage('ce-family');}
  const pages={'ce-brands':listBrands,'ce-brand':brandDetail,'ce-categories':listCategories,'ce-category':categoryDetail,'ce-attributes':listAttributes,'ce-families':listFamilies,'ce-family':familyDetail,'ce-product':productDetail};
  async function action(raw,id=''){
    if(!raw.startsWith('ce-'))return false;
    const name=raw.slice(3);
    if(name==='brand'){await openBrand(id);return true}
    if(name==='category'){await openCategory(id);return true}
    if(name==='product'){await openProduct(id);return true}
    if(name==='family'){await openFamily(id);return true}
    if(name==='open-family'){await openFamily(id);return true}
    if(name==='back-brands'){setPage('ce-brands');return true}
    if(name==='back-categories'){setPage('ce-categories');return true}
    if(name==='back-families'){setPage('ce-families');return true}
    if(name==='back-products'){setPage('catalog');return true}
    if(name==='prev'||name==='next'){state.p=Math.max(1,state.p+(name==='next'?1:-1));await load();return true}
    if(name==='search-brands'||name==='search-families'){state.p=1;state.q=document.querySelector('#ce-q')?.value.trim()||'';await load();return true}
    if(name==='rename-brand'){const p=await api(`${E}/brands/${state.brand}/profile`);await modal('Название производителя','Slug и товарные UUID не изменяются',[fld('name','Название','text',true,{value:p.name})],b=>api(`${E}/brands/${state.brand}`,{method:'PATCH',body:JSON.stringify(b)}));return true}
    if(name==='edit-brand-profile'){const p=(await api(`${E}/brands/${state.brand}/profile`)).profile;await modal('Профиль бренда','Проверка бренда выполняется только оператором',[fld('legal_name','Юридическое название','text',false,{value:p.legal_name||''}),fld('website','Сайт HTTPS','text',false,{value:p.website||''}),fld('country_code','Код страны (ISO, например RU)','text',false,{value:p.country_code||''}),fld('description','Описание','textarea',false,{value:p.description||''}),fld('verified','Подтверждён','checkbox',false,{value:p.verified})],b=>api(`${C}/brands/${state.brand}/profile`,{method:'PUT',body:JSON.stringify({legal_name:b.legal_name||null,website:b.website||null,country_code:b.country_code||null,description:b.description||null,verified:b.verified})}));return true}
    if(name==='new-alias'){const suppliers=await api('/api/v1/backoffice/workspace/suppliers');await modal('Псевдоним производителя','Псевдоним не связывает автоматически товары',[fld('alias','Альтернативное название'),fld('supplier_id','Поставщик (необязательно)','select',false,{options:suppliers.map(s=>[s.id,s.name||s.code])}),fld('verified','Подтверждён','checkbox',false)],b=>api(`${C}/brands/${state.brand}/aliases`,{method:'POST',body:JSON.stringify(b)}));return true}
    if(name==='verify-alias'){const d=await api(`${C}/brands/${state.brand}/aliases`);const a=d.items.find(x=>x.id===id);if(!a)throw Error('Псевдоним не найден');if(!confirm(a.verified?'Отозвать подтверждение?':'Подтвердить псевдоним производителя?'))return true;await api(`${C}/brands/${state.brand}/aliases/${id}?verified=${!a.verified}`,{method:'PATCH'});await load();return true}
    if(name==='delete-alias'){if(!confirm('Удалить псевдоним?'))return true;await api(`${C}/brands/${state.brand}/aliases/${id}`,{method:'DELETE'});await load();return true}
    if(name==='new-category'||name==='edit-category'||name==='toggle-category'){
      const all=(await api(C+'/categories')).items;const cat=name==='new-category'?null:all.find(x=>x.id===state.category);if(!cat&&name!=='new-category')throw Error('Категория отсутствует');
      const body=name==='toggle-category'?{name:cat.name,parent_id:cat.parent_id,position:cat.position,active:!cat.active}:null;
      if(body){if(!confirm((body.active?'Включить':'Выключить')+' категорию?'))return true;await api(`${C}/categories/${cat.id}`,{method:'PUT',body:JSON.stringify(body)});await load();return true}
      await modal(cat?'Редактирование категории':'Новая категория','Подкатегория наследует характеристики родителя',[
        fld('name','Название','text',true,{value:cat?.name||''}),...(cat?[]:[fld('slug','Slug категории')]),fld('parent_id','Родительская категория','select',false,{options:all.filter(x=>x.id!==cat?.id).map(x=>[x.id,x.name]),value:cat?.parent_id||''}),fld('position','Порядок','number',true,{value:cat?.position??0}),fld('active','Активная','checkbox',false,{value:cat?.active??true})],b=>api(cat?`${C}/categories/${cat.id}`:C+'/categories',{method:cat?'PUT':'POST',body:JSON.stringify({...b,parent_id:b.parent_id||null})}));return true}
    if(name==='new-attribute'||name==='attribute'||name==='create-category-attribute'||name==='create-product-attribute'){
      const isEdit=name==='attribute';const isBoundCreate=name==='create-category-attribute'||name==='create-product-attribute';
      const cid=name==='create-product-attribute'?(await api(`${E}/products/${state.product}/attribute-form`)).primary_category_id:state.category;
      if(isBoundCreate&&!cid)throw Error('У товара должна быть основная категория, чтобы создавать характеристики');
      const defs=(await api(C+'/attributes')).items;const a=isEdit?defs.find(x=>x.id===id):null;
      if(isEdit&&!a)throw Error('Характеристика отсутствует');
      const info=isEdit?await api(`${E}/attributes/${id}/usage`):null;
      const fieldOpts=['text','integer','decimal','boolean','enum'].map(v=>[v,({text:'Текст',integer:'Целое число',decimal:'Десятичное число',boolean:'Да/нет',enum:'Список значений'})[v]]);
      const observed=(info?.observed_values||[]).map(String);
      const savedOptions=a?.enum_values||[];
      const suggestedOptions=savedOptions.length?savedOptions:observed;
      const fields=[fld('code','Код характеристики (логический ID)','text',true,{value:a?.code||''}),fld('name','Название','text',true,{value:a?.name||''}),
        fld('data_type','Тип значения','select',true,{options:fieldOpts,value:a?.data_type||'text'}),
        fld('unit','Единица измерения (для чисел)','text',false,{value:a?.unit||''}),
        fld('enum_values','Варианты списка — каждый с новой строки','textarea',false,{value:suggestedOptions.join('\n')}),
        fld('filterable','Разрешить использовать в фильтрах','checkbox',false,{value:a?.filterable??false}),
        fld('active','Активна','checkbox',false,{value:a?.active??true}),
        ...(isBoundCreate?[fld('required','Обязательна для публикации','checkbox',false),fld('show_in_filters','Показать в фильтрах категории','checkbox',false)]:[]),
        ...(isEdit?[fld('confirm_type_change','Подтверждаю перенос всех сохранённых значений при смене типа','checkbox',false)]:[])];
      const description=isEdit?`Неизменяемый UUID: ${info.immutable_uuid}. Категорий: ${info.category_bindings}; товаров со значением: ${info.product_values}. Известные значения: ${observed.length}${info.observed_values_truncated?' (не все!)':''}. При переводе в список все используемые значения должны входить в варианты.`:
        'Логический ID (code) можно переименовать позднее. UUID создаётся автоматически и никогда не изменяется.';
      await modal(isEdit?'Изменить характеристику':isBoundCreate?'Добавить характеристику категории':'Новая характеристика',description,fields,b=>{
        const type=b.data_type;
        // One option per line is preferred; comma-separated input remains supported.
        const input=String(b.enum_values||'').trim();
        const tokens=(input.includes('\n')||observed.includes(input)||savedOptions.includes(input))?input.split(/\r?\n/):input.split(',');
        const unique=[];const seen=new Set();
        for(const token of tokens){const item=token.trim();if(!item||seen.has(item))continue;seen.add(item);unique.push(item);}
        if(type==='enum'){
          if(!unique.length)throw Error('Для типа «Список значений» укажи варианты: по одному на строку, например «Красный» и «Синий».');
          if(unique.length>80)throw Error('У списка может быть не более 80 вариантов.');
          if(unique.some(x=>x.length>160))throw Error('Вариант списка не может быть длиннее 160 символов.');
          if(info?.observed_values_truncated)throw Error('У характеристики больше 80 разных значений. Нужна отдельная проверка перед переводом в enum.');
          if(isEdit&&a.data_type==='text'){
            const absent=observed.filter(x=>!seen.has(x));
            if(absent.length)throw Error('Нельзя перевести в enum: в товарах уже используются значения «'+absent.slice(0,5).join('», «')+'». Добавь их в список без изменения написания.');
          }
        }
        const payload={code:b.code,name:b.name,data_type:type,unit:['integer','decimal'].includes(type)?(b.unit||null):null,
          enum_values:type==='enum'?unique:null,
          filterable:!!b.filterable,active:!!b.active};
        if(isEdit){return api(`${E}/attributes/${id}/schema`,{method:'PUT',body:JSON.stringify({...payload,
          expected_code:a.code,expected_data_type:a.data_type,confirm_type_change:!!b.confirm_type_change})});}
        if(isBoundCreate){return api(`${E}/categories/${cid}/attribute-definition`,{method:'POST',body:JSON.stringify({...payload,required:!!b.required,show_in_filters:!!b.show_in_filters})});}
        return api(C+'/attributes',{method:'POST',body:JSON.stringify(payload)});
      });
      // Progressive form: options and units only appear for suitable types.
      const form=document.querySelector('#modal-form');
      const typeSelect=form?.querySelector('[name="data_type"]');
      const optionsInput=form?.querySelector('[name="enum_values"]');
      const unitInput=form?.querySelector('[name="unit"]');
      if(typeSelect&&optionsInput&&unitInput){
        const optionsLabel=optionsInput.closest('label');const unitLabel=unitInput.closest('label');
        const hint=document.createElement('small');hint.style.display='block';hint.style.marginTop='5px';
        hint.textContent='Существующие значения предложены автоматически. Каждый вариант — отдельная строка. Добавь другие цвета вручную. Дубликаты удаляются при сохранении.';
        optionsLabel.appendChild(hint);
        const updateFields=()=>{const t=typeSelect.value;optionsLabel.style.display=t==='enum'?'':'none';unitLabel.style.display=['integer','decimal'].includes(t)?'':'none';};
        typeSelect.addEventListener('change',updateFields);updateFields();
      }
      return true;
    }
    if(name==='delete-attribute'){
      const u=await api(`${E}/attributes/${id}/usage`);
      await modal('Удаление характеристики: '+u.attribute.name,
        `UUID: ${u.immutable_uuid}. Привязок к категориям: ${u.category_bindings}, заполненных товаров: ${u.product_values}. Удаление возможно только без зависимостей. Иначе сначала отвяжи категории и удали значения или отключи характеристику.`,
        [fld('confirm','Подтверждаю удаление характеристики','checkbox',false)],
        b=>{if(!b.confirm)throw Error('Подтверди удаление');return api(`${E}/attributes/${id}`,{method:'DELETE'});});return true;
    }
    if(name==='bind-attribute'){const a=(await api(C+'/attributes')).items.filter(x=>x.active);await modal('Привязать характеристику','Шаблон категории', [fld('attribute_id','Характеристика','select',true,{options:a.map(x=>[x.id,`${x.code} · ${x.name}`])}),fld('required','Обязательна при публикации','checkbox',false)],b=>api(`${C}/categories/${state.category}/attributes`,{method:'POST',body:JSON.stringify(b)}));return true}
    if(name==='template-binding'){
      const defs=(await api(C+'/attributes')).items;
      const data=await api(`${E}/categories/${state.category}/template`);
      const bind=data.items.find(x=>x.attribute_id===id);const def=defs.find(x=>x.id===id);
      if(!bind||!def)throw Error('Характеристика отсутствует в шаблоне');
      await modal('Настройки категории: '+def.name,'Поля наследуются от родителей. Значения существующих товаров не изменяются.',[
        fld('required','Обязательно при публикации','checkbox',false,{value:bind.required}),
        fld('show_in_filters','Показывать в фильтрах','checkbox',false,{value:bind.show_in_filters&&def.filterable}),
        fld('group_name','Группа полей','text',true,{value:bind.group_name||'Основные'}),
        fld('sort_order','Порядок отображения','number',true,{value:bind.sort_order??0})
      ],b=>api(`${E}/categories/${state.category}/template/${id}`,{method:'PUT',body:JSON.stringify({...b,sort_order:Number(b.sort_order)})}));return true}
    if(name==='template-category'){await openCategory(id);return true}
    if(name==='fill-template'){
      const d=await api(`${E}/products/${state.product}/attribute-form`);
      const candidates=d.items.filter(x=>x.active);
      if(!candidates.length){toast('Сначала назначь характеристики в категории',true);return true;}
      const fields=[];
      candidates.forEach((a,i)=>{
        const title=(a.required?'* ':'')+a.name+(a.unit?' ('+a.unit+')':'')+' · '+a.group_name;
        fields.push(fld('operation_'+i,title+' — действие','select',true,{value:'skip',options:[
          ['skip','Не менять'],['set',a.value===null?'Добавить значение':'Изменить значение'],
          ...(a.value!==null?[['remove','Удалить значение только у товара']]:[])]}));
        const current=a.value===null?'':String(a.value);
        fields.push(fld('value_'+i,'Значение: '+a.name,a.data_type==='enum'||a.data_type==='boolean'?'select':'text',false,
          a.data_type==='boolean'?{value:current,options:[['true','Да'],['false','Нет']]}:
          a.data_type==='enum'?{value:current,options:(a.enum_values||[]).map(v=>[v,v])}:{value:current}));
      });
      await modal('Характеристики товара — массовое редактирование',
        'Для каждого поля выбери действие. Только выбранные операции сохранятся одной транзакцией. Удаление обязательного значения у опубликованного товара запрещено.',fields,b=>{
        const changes=[];
        candidates.forEach((a,i)=>{
          const action=b['operation_'+i]||'skip';if(action==='skip')return;
          if(action==='remove'){changes.push({attribute_id:a.attribute_id,action,expected_value:a.value});return;}
          const raw=b['value_'+i];if(raw==null||raw==='')throw Error('Заполни значение: '+a.name);
          let value=raw;
          if(a.data_type==='integer'){if(!/^-?\d+$/.test(String(raw)))throw Error('Нужно целое число: '+a.name);value=Number(raw);if(!Number.isSafeInteger(value))throw Error('Число вне допустимого диапазона: '+a.name);}
          if(a.data_type==='boolean')value=raw==='true';
          changes.push({attribute_id:a.attribute_id,action:'set',value,expected_value:a.value});
        });
        if(!changes.length)throw Error('Выбери действие хотя бы для одной характеристики');
        if(changes.some(x=>x.action==='remove')&&!confirm('Удалить выбранные значения только у этого товара? Определения характеристик останутся.'))throw Error('Удаление отменено');
        return api(`${E}/products/${state.product}/attribute-changes`,{method:'PATCH',body:JSON.stringify({changes})});
      });
      return true;
    }
    if(name==='toggle-required'){const d=await api(`${E}/categories/${state.category}/bindings`);const bind=d.items.find(x=>x.attribute_id===id&&x.direct);if(!bind)throw Error('Привязка отсутствует');await api(`${E}/categories/${state.category}/attributes/${id}`,{method:'PUT',body:JSON.stringify({required:!bind.required})});await load();return true}
    if(name==='unbind-attribute'){if(!confirm('Отвязать характеристику? Значения товаров остаются в БД.'))return true;await api(`${C}/categories/${state.category}/attributes/${id}`,{method:'DELETE'});await load();return true}
    if(name==='unpublish-product'){if(!confirm('Снять товар с публикации? Он исчезнет из публичного каталога, UUID и история SEO сохранятся.'))return true;await api(`${E}/products/${state.product}/unpublish`,{method:'POST'});toast('Товар переведён в черновики');await load();return true}
    if(name==='edit-product'){const p=await api(`${E}/products/${state.product}/identity`);await modal('Описание товара','Бренд, артикул и UUID сохраняются неизменными',[fld('name','Название','text',true,{value:p.name}),fld('description','Описание','textarea',false,{value:p.description||''})],b=>api(`${E}/products/${state.product}/content`,{method:'PATCH',body:JSON.stringify({name:b.name,description:b.description||null})}));return true}
    if(name==='publish-product'){if(!confirm('Опубликовать товар? Проверьте категории, характеристики и состав.'))return true;await api(`${B}/products/${state.product}/publish`,{method:'POST'});toast('Товар опубликован');await load();return true}
    if(name==='assign-category'){const [d,p]=await Promise.all([api(C+'/categories'),api(`${E}/products/${state.product}/identity`)]);const used=new Set(p.categories.map(x=>x.category_id));await modal('Категория товара','Выберите категорию',[fld('category_id','Категория','select',true,{options:d.items.filter(x=>x.active&&!used.has(x.id)).map(x=>[x.id,x.name])}),fld('is_primary','Основная категория','checkbox',false)],b=>api(`${C}/products/${state.product}/categories`,{method:'POST',body:JSON.stringify(b)}));return true}
    if(name==='remove-product-category'){if(!confirm('Удалить категорию из карточки товара?'))return true;await api(`${C}/products/${state.product}/categories/${id}`,{method:'DELETE'});await load();return true}
    if(name==='choose-attribute'){const [defs,cats,p]=await Promise.all([api(C+'/attributes'),api(C+'/categories'),api(`${E}/products/${state.product}/identity`)]);const seen=new Set();for(const assigned of p.categories){let cur=cats.items.find(x=>x.id===assigned.category_id);while(cur&&!seen.has(cur.id)){seen.add(cur.id);cur=cats.items.find(x=>x.id===cur.parent_id)}}const links=await Promise.all([...seen].map(id=>api(`${E}/categories/${id}/bindings`)));const usable=new Set(links.flatMap(x=>x.items.map(y=>y.attribute_id)));await modal('Выберите характеристику','Атрибуты должны быть назначены категории товара',[fld('attribute_id','Характеристика','select',true,{options:defs.items.filter(x=>usable.has(x.id)&&x.active).map(x=>[x.id,x.name])})],async b=>{state.attribute=b.attribute_id;setTimeout(()=>setValueForm(b.attribute_id).catch(e=>toast(e.message,true)),120)});return true}
    if(name==='set-product-attribute'){await setValueForm(id);return true}
    if(name==='remove-product-attribute'){const data=await api(`${E}/products/${state.product}/attribute-form`);const field=data.items.find(x=>x.attribute_id===id);if(!field||field.value===null)throw Error('Текущего значения нет');if(!confirm('Удалить только значение «'+field.name+'» у товара? Определение и шаблон категории останутся.'))return true;await api(`${E}/products/${state.product}/attribute-changes`,{method:'PATCH',body:JSON.stringify({changes:[{attribute_id:id,action:'remove',expected_value:field.value}]})});await load();return true}
    if(name==='new-family'){await modal('Новое семейство','Например: фасовки 1/3/5 литров',[fld('name','Название'),fld('slug','Slug'),fld('kind','Тип','select',true,{options:[['variant','Варианты'],['kit_line','Линейка комплектов'],['related_line','Связанные товары']]}),fld('axes','Оси через запятую (например volume_l)')],b=>api(M+'/families',{method:'POST',body:JSON.stringify({...b,axes:b.axes.split(',').map(x=>x.trim()).filter(Boolean)})}));return true}
    if(name==='attach-family'){const d=await api(E+'/families?limit=100');await modal('Привязать к семейству','Можно выбрать только одно основное семейство',[fld('family_id','Семейство','select',true,{options:d.items.map(x=>[x.id,familyLabel(x)])})],async b=>{state.family=b.family_id;const fam=await api(E+'/families/'+b.family_id);setTimeout(()=>addMember(fam,state.product).catch(e=>toast(e.message,true)),120)});return true}
    if(name==='new-member'){await addMember(await api(E+'/families/'+state.family));return true}
    if(name==='remove-member'){if(!confirm('Удалить товар из семейства? Товар останется в каталоге.'))return true;await api(`${M}/families/${state.family}/members/${id}`,{method:'DELETE'});await load();return true}
    if(name==='composition'){const d=await api(`${M}/products/${state.product}/links`);await modal('Состав товара','Самостоятельно собираемые комплекты не публикуются',[fld('kind','Тип','select',true,{options:[['factory_kit','Заводской комплект'],['assembled_plan','Сборный набор (только черновик)']],value:d.composition?.kind||'factory_kit'}),fld('verified','Проверен','checkbox',false,{value:d.composition?.verified||false})],b=>api(`${M}/products/${state.product}/composition`,{method:'PUT',body:JSON.stringify(b)}));return true}
    if(name==='new-component'){await modal('Компонент комплекта','Связанный товар выбирается поиском',[fld('position','Позиция','number',true,{value:1}),fld('component_product_id','Товар','product',false),fld('name','Описание компонента'),fld('external_part_number','Заводской номер','text',false),fld('quantity','Количество','number',true,{value:1}),fld('unit','Единица','select',true,{options:[['pcs','шт.'],['kg','кг'],['l','л'],['m','м']]}),fld('sale_status','Отдельная продажа','select',true,{options:[['unknown','Неизвестно'],['not_listed','Не продаётся'],['independent','Есть отдельный товар']]} )],b=>api(`${M}/products/${state.product}/components`,{method:'POST',body:JSON.stringify({...b,component_product_id:b.component_product_id||null,external_part_number:b.external_part_number||null})}));return true}
    if(name==='remove-component'){if(!id){toast('Не найден ID компонента',true);return true}if(!confirm('Удалить компонент из состава?'))return true;await api(`${M}/products/${state.product}/components/${id}`,{method:'DELETE'});await load();return true}
    if(name==='new-relation'){await modal('Связать товары','UUID выбирается через поиск',[fld('related_product_id','Другой товар','product'),fld('relation','Связь','select',true,{options:[['related','Связанный'],['alternative','Аналог'],['supersedes','Замена'],['accessory','Аксессуар'],['paired','Парный'],['equivalent','Эквивалент']]}),fld('verified','Подтверждена','checkbox',false)],b=>api(M+'/relations',{method:'POST',body:JSON.stringify({...b,product_id:state.product})}));return true}
    if(name==='verify-relation'||name==='verify-package'){const links=await api(`${M}/products/${state.product}/links`);const row=name==='verify-relation'?links.relations.find(x=>x.id===id):links.factory_packages.find(x=>x.id===id);if(!row)throw Error('Связь не найдена');if(!confirm('Изменить статус проверки?'))return true;await api(`${M}/${name==='verify-relation'?'relations':'packages'}/${id}`,{method:'PATCH',body:JSON.stringify({verified:!row.verified})});await load();return true}
    if(name==='remove-relation'||name==='remove-package'){if(!confirm('Удалить связь?'))return true;await api(`${M}/${name==='remove-relation'?'relations':'packages'}/${id}`,{method:'DELETE'});await load();return true}
    if(name==='new-package'){await modal('Заводская упаковка','Внешний и внутренний товар имеют разные UUID',[fld('outer_product_id','Упаковка (Product)','product'),fld('inner_product_id','Товар внутри (Product)','product'),fld('units_per_package','Штук в упаковке','number',true,{value:2}),fld('kind','Тип','select',true,{options:[['retail_pack','Розничная'],['box','Короб'],['case','Кейс'],['pallet','Паллета']]}),fld('verified','Подтверждена','checkbox',false)],b=>api(M+'/packages',{method:'POST',body:JSON.stringify(b)}));return true}
    if(name==='edit-offer'){const d=await api(`${E}/products/${state.product}/sale-terms`);const offer=d.seller_offers.find(x=>x.offer_id===id);if(!offer)throw Error('Предложение не найдено');await modal('Цена и статус предложения',offer.sku+' · изменение только этого продавца',[fld('price','Розничная цена, ₽','number',true,{value:offer.price}),fld('status','Статус','select',true,{options:[['draft','Черновик'],['active','Активно'],['paused','Приостановлено'],['archived','Архив']],value:offer.status})],b=>api(`${W}/offers/${id}`,{method:'PATCH',body:JSON.stringify(b)}));return true}
    if(name==='edit-stock'){const d=await api(`${E}/products/${state.product}/sale-terms`);const offer=d.seller_offers.find(x=>x.offer_id===id);if(!offer)throw Error('Предложение не найдено');if(!offer.stock_by_node?.length)throw Error('Нет привязки к складу. Создайте её в разделе Остатки.');await modal('Остаток собственного склада',offer.sku+' · резерв нельзя списывать ниже нуля',[fld('node_id','Склад','select',true,{options:offer.stock_by_node.map(x=>[x.node_id,x.city+' · '+x.node])}),fld('on_hand','Физический остаток','number',true,{value:offer.stock_by_node[0].on_hand})],b=>api(`${W}/offers/${id}/stock`,{method:'PUT',body:JSON.stringify(b)}));return true}
    if(name==='sale-terms'){const d=await api(`${E}/products/${state.product}/sale-terms`);const offer=d.seller_offers.find(x=>x.offer_id===id);if(!offer)throw Error('Предложение не найдено');const t=offer.terms||{};await modal('Условия продажи','Правило только для этого предложения',[fld('minimum_units','Минимальная партия','number',true,{value:t.minimum_units||1}),fld('step_units','Шаг заказа','number',true,{value:t.step_units||1}),fld('units_per_package','Единиц в пачке','number',true,{value:t.units_per_package||1}),fld('order_unit','Единица продажи','select',true,{options:[['piece','Штуки'],['pack','Упаковки']],value:t.order_unit||'piece'}),fld('status','Статус','select',true,{options:[['draft','Черновик'],['active','Активно']],value:t.status||'draft'})],b=>api(`${M}/sale-terms/seller/${id}`,{method:'PUT',body:JSON.stringify(b)}));return true}
    if(name==='media-edit'){const row=await api(`${C}/media/${id}`);await modal('Медиафайл','Одобрение делает файл публичным только с опубликованным товаром',[fld('status','Статус','select',true,{options:[['draft','Черновик'],['approved','Одобрен'],['rejected','Отклонён']],value:row.status}),fld('position','Порядок','number',true,{value:row.position}),fld('alt_text','Описание','text',false,{value:row.alt_text||''})],b=>api(`${C}/media/${id}?`+new URLSearchParams(b),{method:'PATCH'}));return true}
    if(name==='media-download'){const row=await api(`${C}/media/${id}`);const blob=await apiBlob(`${E}/media/${id}/preview?variant=original`);const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=row.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);return true}
    return true;
  }
  async function setValueForm(id){
    const form=await api(`${E}/products/${state.product}/attribute-form`);
    const a=form.items.find(x=>x.attribute_id===id);
    if(!a)throw Error('Характеристика не назначена категории товара');
    const current=a.value;
    const field=a.data_type==='enum'?fld('value',a.name,'select',true,{options:(a.enum_values||[]).map(v=>[v,v]),value:current}):
      a.data_type==='boolean'?fld('value',a.name,'select',true,{options:[['true','Да'],['false','Нет']],value:current===null?'':String(current)}):
      fld('value',a.name,a.data_type==='integer'||a.data_type==='decimal'?'text':'text',true,{value:current??''});
    await modal((current===null?'Добавить: ':'Изменить: ')+a.name,
      'UUID характеристики неизменяем. Тип и варианты определяет категория. Сохранение не меняет другие значения товара.',[field],b=>{
        let value=b.value;
        if(value==null||String(value).trim()==='')throw Error('Укажи значение характеристики');
        if(a.data_type==='boolean')value=value==='true';
        if(a.data_type==='integer'){
          if(!/^-?\d+$/.test(String(value)))throw Error('Укажи целое число');
          value=Number(value);if(!Number.isSafeInteger(value))throw Error('Число вне допустимого диапазона');
        }
        return api(`${E}/products/${state.product}/attribute-changes`,{method:'PATCH',body:JSON.stringify({changes:[
          {attribute_id:id,action:'set',value,expected_value:current}
        ]})});
      });
  }
  async function addMember(fam,productId=''){
    const fields=[...(productId?[]:[fld('product_id','Товар','product')]),...fam.axes.map(a=>fld(a,'Значение оси: '+a))];
    await modal('Привязка к семейству',fam.name,fields,b=>api(`${M}/families/${fam.id}/members`,{method:'POST',body:JSON.stringify({product_id:productId||b.product_id,axis_values:Object.fromEntries(fam.axes.map(a=>[a,b[a]]))})}));
  }
  async function upload(e){const f=e.target;if(f.id!=='ce-upload')return false;
    e.preventDefault();const form=new FormData(f),owner=f.dataset.owner;form.set(owner+'_id',f.dataset.id);
    const button=f.querySelector('[type=submit]');button.disabled=true;
    try {await api(C+'/media',{method:'POST',body:form});toast('Файл загружен; требуется модерация');await load()}
    catch(err){toast(err.message,true)}finally{button.disabled=false}return true;
  }
  return {pages,action,openProduct,upload, resetPreview};
};
