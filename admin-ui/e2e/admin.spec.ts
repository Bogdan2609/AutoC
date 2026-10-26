/** UI smoke tests with intercepted API. Never reach a real supplier, payments or database. */
import {test, expect, type Page} from '@playwright/test'

const rules = [
 {prefix:'/api/v1/admin/staff/sessions',scope:'sessions'},
 {prefix:'/api/v1/admin/staff',scope:'staff'},
 {prefix:'/api/v1/backoffice/workspace/catalog',scope:'catalog'},
 {prefix:'/api/v1/backoffice/products',scope:'catalog'},
 {prefix:'/api/v1/backoffice/monitor',scope:'system'},
 {prefix:'/api/v1/backoffice/workspace/summary',scope:'analytics'},
 {prefix:'/api/v1/backoffice/catalog-v021',scope:'catalog'},
 {prefix:'/api/v1/backoffice/operations',scope:'operations'},
]
const basePerms = Object.fromEntries(['analytics','catalog','orders','sessions','staff','suppliers','marketplace','warehouses','system','finance','operations','purchases','payments_admin'].map(scope=>[scope,{read:false,write:false}]))
function fixture(role:'viewer'|'owner') {
 const permissions=structuredClone(basePerms)
 for(const scope of role==='owner'?Object.keys(permissions):['analytics','catalog','sessions','warehouses','marketplace']){
  permissions[scope]={read:true,write:role==='owner'||scope==='sessions'}
 }
 return {staff:{id:'staff-1',user_id:'user-1',email:'test@example.com',display_name:'Test Operator',enabled:true,role},csrf:'test-only-csrf',permissions,scope_rules:rules}
}
async function mockApp(page:Page,role:'viewer'|'owner'){
 await page.route('**/api/v1/**',route=>route.fulfill({status:200,json:{items:[],total:0}}))
 await page.route('**/api/v1/admin/staff/session',route=>route.fulfill({status:200,json:fixture(role)}))
}

test('staff permissions block a direct link to orders for a viewer',async({page})=>{
 await mockApp(page,'viewer')
 await page.goto('/admin/orders')
 await expect(page.getByText('Нет доступа к разделу')).toBeVisible()
 await expect(page.getByRole('link',{name:'Заказы'})).toHaveCount(0)
})

test('viewer sees catalogue but not create actions',async({page})=>{
 await mockApp(page,'viewer')
 await page.goto('/admin/catalog/manage')
 await expect(page.getByText('Управление мастер-каталогом')).toBeVisible()
 await expect(page.getByRole('button',{name:'Создать'})).toHaveCount(0)
})

test('staff can list and revoke their other admin sessions with explicit confirmation',async({page})=>{
 await mockApp(page,'owner')
 await page.route('**/api/v1/admin/staff/sessions',route=>route.fulfill({status:200,json:[
  {id:'00000000-0000-4000-8000-000000000001',created_at:'2026-10-23T08:00:00Z',expires_at:'2026-10-23T16:00:00Z',current:true},
  {id:'00000000-0000-4000-8000-000000000002',created_at:'2026-10-23T08:20:00Z',expires_at:'2026-10-23T16:20:00Z',current:false},
 ]}))
 let revoked=false
 await page.route('**/api/v1/admin/staff/sessions/revoke-others',route=>{revoked=true;return route.fulfill({status:200,json:{revoked:1}})})
 page.on('dialog',dialog=>dialog.accept())
 await page.goto('/admin/system/sessions')
 await expect(page.getByText('Мои административные сеансы')).toBeVisible()
 await page.getByRole('button',{name:'Завершить другие сеансы'}).click()
 await expect.poll(()=>revoked).toBe(true)
})

test('viewer can rotate own password, then every admin login is required again',async({page})=>{
 await mockApp(page,'viewer')
 let active=true
 await page.route('**/api/v1/admin/staff/session',route=>active
  ?route.fulfill({status:200,json:fixture('viewer')})
  :route.fulfill({status:401,json:{detail:'Session expired'}}))
 let payload:{current_password:string;new_password:string}|undefined
 await page.route('**/api/v1/admin/staff/change-password',async route=>{
  payload=route.request().postDataJSON()
  active=false
  await route.fulfill({status:200,json:{status:'password_changed',sessions_revoked:true}})
 })
 await page.goto('/admin/system/security')
 await expect(page.getByRole('heading',{name:'Безопасность входа'})).toBeVisible()
 await page.getByLabel('Текущий пароль').fill('old-credential-only-for-test')
 await page.getByLabel('Новый пароль',{exact:true}).fill('new-credential-long-only-for-test')
 await page.getByLabel('Повторите новый пароль').fill('new-credential-long-only-for-test')
 await page.getByRole('button',{name:'Обновить пароль и завершить сеансы'}).click()
 await expect.poll(()=>payload?.new_password).toBe('new-credential-long-only-for-test')
 await expect(page.getByRole('heading',{name:'Вход в Autocodex'})).toBeVisible()
})


test('owner can publish a catalogue draft only after typing confirmation',async({page})=>{
 await mockApp(page,'owner')
 const id='00000000-0000-4000-8000-000000000011'
 const identity={id,name:'Тестовая деталь',brand:'TestBrand',article:'ABC-123',slug:'testbrand-abc-123',
  status:'draft',description:'',seo_title:'',seo_description:'',seo_h1:'',seo_noindex:false,
  updated_at:'2026-10-26T10:00:00Z',attributes:[],categories:[],missing_required:[]}
 await page.route(`**/api/v1/backoffice/catalog-editor/products/${id}/identity`,route=>route.fulfill({status:200,json:identity}))
 let posted=0
 await page.route(`**/api/v1/backoffice/products/${id}/publish`,route=>{
  posted+=1
  expect(route.request().method()).toBe('POST')
  return route.fulfill({status:200,json:{...identity,status:'published'}})
 })
 await page.goto(`/admin/catalog/${id}`)
 await page.getByRole('tab',{name:'Публикация'}).click()
 const publish=page.getByRole('button',{name:'Опубликовать товар'})
 await expect(publish).toBeDisabled()
 await page.getByLabel('Введите «ОПУБЛИКОВАТЬ» для подтверждения').fill('ОПУБЛИКОВАТЬ')
 await publish.click()
 await expect.poll(()=>posted).toBe(1)
})

test('viewer cannot execute catalogue publication',async({page})=>{
 await mockApp(page,'viewer')
 const id='00000000-0000-4000-8000-000000000012'
 await page.route(`**/api/v1/backoffice/catalog-editor/products/${id}/identity`,route=>route.fulfill({status:200,json:{
  id,name:'Тестовая деталь',brand:'TestBrand',article:'ABC-124',slug:'testbrand-abc-124',status:'draft',
  description:'',seo_title:'',seo_description:'',seo_h1:'',seo_noindex:false,
  updated_at:'2026-10-26T10:00:00Z',attributes:[],categories:[],missing_required:[]}}))
 await page.goto(`/admin/catalog/${id}`)
 await page.getByRole('tab',{name:'Публикация'}).click()
 await expect(page.getByText('У вашей роли нет права изменять статус публикации.')).toBeVisible()
 await expect(page.getByRole('button',{name:'Опубликовать товар'})).toHaveCount(0)
})

test('purchase attempts on an order use an exact server-side order filter',async({page})=>{
 await mockApp(page,'owner')
 const id='00000000-0000-4000-8000-000000000013'
 await page.route(`**/api/v1/backoffice/operations/orders/${id}`,route=>route.fulfill({status:200,json:{
  id,status:'fulfillment_review',total:'100.00',total_is_final:true,items:[],payments:[]}}))
 await page.route(`**/api/v1/backoffice/monitor/orders/${id}/events`,route=>route.fulfill({status:200,json:{events:[],limit:100,evidence_redacted:true}}))
 let filtered=false
 await page.route('**/api/v1/backoffice/monitor/purchases?*',route=>{
  const url=new URL(route.request().url());filtered=url.searchParams.get('order_id')===id
  return route.fulfill({status:200,json:{page:1,size:20,total:0,items:[]}})
 })
 await page.goto(`/admin/orders/${id}`)
 await expect(page.getByText('Попытки закупки по этому заказу')).toBeVisible()
 await expect.poll(()=>filtered).toBe(true)
 await expect(page.getByText('Для этого заказа попыток закупки не найдено')).toBeVisible()
})

test('v0.12 purchase attempt detail is an exact, redacted, GET-only diagnostic',async({page})=>{
 await mockApp(page,'owner')
 const order='00000000-0000-4000-8000-000000000041'
 const attempt='00000000-0000-4000-8000-000000000042'
 await page.route(`**/api/v1/backoffice/operations/orders/${order}`,route=>route.fulfill({status:200,json:{
  id:order,status:'procurement_manual_review',total:'110.00',total_is_final:true,items:[],payments:[]}}))
 await page.route(`**/api/v1/backoffice/monitor/orders/${order}/events`,route=>route.fulfill({status:200,json:{events:[],limit:100,evidence_redacted:true}}))
 await page.route('**/api/v1/backoffice/monitor/purchases?*',route=>route.fulfill({status:200,json:{
  items:[{id:attempt,order_id:order,order_line_id:null,account_id:'account-1',destination_id:'dest-1',status:'dispatch_unknown',remote_order_ids:[],error_code:'TIMEOUT',created_at:'2026-10-26T08:00:00Z'}],page:1,size:20,total:1}}))
 let reads=0,unexpectedWrites=0
 await page.route(`**/api/v1/backoffice/monitor/purchases/${attempt}`,route=>{
  if(route.request().method()!=='GET')unexpectedWrites++
  else reads++
  return route.fulfill({status:200,json:{id:attempt,order_id:order,order_line_id:null,
   account_id:'account-1',destination_id:'dest-1',status:'dispatch_unknown',remote_order_ids:[],error_code:'TIMEOUT',quantity:1,
   created_at:'2026-10-26T08:00:00Z',sent_at:'2026-10-26T08:00:05Z',completed_at:null,
   manual_reconciliation_required:true,advisory:'Never retry unknown result'}})
 })
 await page.goto(`/admin/orders/${order}`)
 await page.getByRole('button',{name:'Детали попытки'}).click()
 await expect(page.getByTestId('purchase-attempt-detail')).toBeVisible()
 await expect(page.getByText('Требуется ручная сверка с поставщиком. Не повторяйте закупку автоматически.')).toBeVisible()
 await expect.poll(()=>reads).toBe(1)
 expect(unexpectedWrites).toBe(0)
 await expect(page.getByRole('button',{name:/повторить закупку/i})).toHaveCount(0)
})

test('v0.12 purchase detail rejects accidental cross-order association',async({page})=>{
 await mockApp(page,'owner')
 const order='00000000-0000-4000-8000-000000000051'
 const attempt='00000000-0000-4000-8000-000000000052'
 await page.route(`**/api/v1/backoffice/operations/orders/${order}`,route=>route.fulfill({status:200,json:{
  id:order,status:'fulfillment_review',total:'100.00',total_is_final:true,items:[],payments:[]}}))
 await page.route(`**/api/v1/backoffice/monitor/orders/${order}/events`,route=>route.fulfill({status:200,json:{events:[],limit:100,evidence_redacted:true}}))
 await page.route('**/api/v1/backoffice/monitor/purchases?*',route=>route.fulfill({status:200,json:{
  items:[{id:attempt,order_id:order,order_line_id:null,account_id:'a',destination_id:'d',status:'accepted',remote_order_ids:[],error_code:null,created_at:'2026-10-26T08:00:00Z'}],page:1,size:20,total:1}}))
 await page.route(`**/api/v1/backoffice/monitor/purchases/${attempt}`,route=>route.fulfill({status:200,json:{
  id:attempt,order_id:'00000000-0000-4000-8000-000000000099',order_line_id:null,account_id:'a',destination_id:'d',status:'accepted',
  remote_order_ids:[],error_code:null,quantity:1,created_at:'2026-10-26T08:00:00Z',sent_at:null,completed_at:null,manual_reconciliation_required:false,advisory:''}}))
 await page.goto(`/admin/orders/${order}`)
 await page.getByRole('button',{name:'Детали попытки'}).click()
 await expect(page.getByText('Попытка не принадлежит открытому заказу. Проверьте ссылку и данные.')).toBeVisible()
})
