/** UI smoke tests with intercepted API. Never reach a real supplier, payments or database. */
import {test, expect, type Page} from '@playwright/test'

const rules = [
 {prefix:'/api/v1/admin/staff/sessions',scope:'sessions'},
 {prefix:'/api/v1/admin/staff',scope:'staff'},
 {prefix:'/api/v1/backoffice/workspace/catalog',scope:'catalog'},
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
