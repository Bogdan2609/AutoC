/* Employee sessions use HttpOnly cookies; the short-lived CSRF value lives in memory. */
export type StaffRole = 'owner'|'operator'|'catalog_manager'|'supplier_manager'|'finance_manager'|'analyst'|'viewer'
export type StaffIdentity = {id:string;user_id:string;email:string;display_name:string;role:StaffRole;enabled:boolean}
export type ScopeGrant={read:boolean;write:boolean}
export type ScopeRule={prefix:string;scope:string}
type StaffLoginResult={staff:StaffIdentity;csrf:string;permissions:Record<string,ScopeGrant>;scope_rules:ScopeRule[]}
let operatorToken: string | null = null
let activeStaff: StaffIdentity | null = null
let csrf: string | null = null
let scopeGrants: Record<string,ScopeGrant> = {}
let scopeRules: ScopeRule[] = []
export function canAccess(scope:string,write=false):boolean{
 if(operatorToken)return true // emergency legacy key is a server-side superadmin
 if(!activeStaff)return false
 const grant=scopeGrants[scope]
 return Boolean(grant && (write?grant.write:grant.read))
}
export function scopeForPath(path:string):string|null{
 return scopeRules.find(r=>path===r.prefix||path.startsWith(r.prefix+'/'))?.scope??null
}
export function canAccessPath(path:string,write=false):boolean{
 if(operatorToken)return true
 const scope=scopeForPath(path.split('?')[0])
 return scope!==null && canAccess(scope,write)
}
const STAFF='/api/v1/admin/staff'
export function setOperatorToken(value:string|null){operatorToken=value;activeStaff=null;csrf=null;scopeGrants={};scopeRules=[]}
export function currentStaff(){return activeStaff}
export function isStaffOwner(){return Boolean(operatorToken || activeStaff?.role==='owner')}
export function clearOperatorAuth(){operatorToken=null;activeStaff=null;csrf=null;scopeGrants={};scopeRules=[]}
export class ApiError extends Error {
  constructor(readonly status:number, detail:string){super(detail);this.name='ApiError'}
}
async function parseApiError(resp:Response){let detail=`HTTP ${resp.status}`;try{const body:unknown=await resp.json();if(body&&typeof body==='object'&&'detail' in body&&typeof body.detail==='string')detail=body.detail}catch{/* no JSON */}return new ApiError(resp.status,detail)}
export async function restoreStaffSession():Promise<boolean>{
 try{
  const resp=await fetch(`${STAFF}/session`,{credentials:'same-origin',cache:'no-store'})
  if(!resp.ok)return false
  const result=await resp.json() as StaffLoginResult
  activeStaff=result.staff;csrf=result.csrf;operatorToken=null;scopeGrants=result.permissions;scopeRules=result.scope_rules;return true
 }catch{return false}
}
export async function loginStaff(email:string,password:string):Promise<StaffIdentity>{
 const resp=await fetch(`${STAFF}/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password}),credentials:'same-origin',cache:'no-store'})
 if(!resp.ok)throw await parseApiError(resp)
 const result=await resp.json() as StaffLoginResult
 activeStaff=result.staff;csrf=result.csrf;operatorToken=null;scopeGrants=result.permissions;scopeRules=result.scope_rules
 return result.staff
}
export async function logoutAuth(){
 if(activeStaff&&csrf){
  try{await fetch(`${STAFF}/logout`,{method:'POST',headers:{'X-Admin-CSRF':csrf},credentials:'same-origin',cache:'no-store'})}finally{clearOperatorAuth()}
 }else clearOperatorAuth()
}
export async function api<T>(path:string,init?:RequestInit):Promise<T>{
 if(!operatorToken&&!activeStaff)throw new ApiError(401,'Выполните вход')
 if(!path.startsWith('/api/v1/'))throw new Error('Запрещённый путь API')
 const mutation=Boolean(init?.method&&!['GET','HEAD','OPTIONS'].includes(init.method.toUpperCase()))
 if(activeStaff&&!canAccessPath(path,mutation))throw new ApiError(403,'Недостаточно прав для этого раздела или действия')
 const headers=new Headers(init?.headers)
 if(operatorToken)headers.set('X-Admin-Token',operatorToken)
 if(!operatorToken&&activeStaff&&csrf&&init?.method&&!['GET','HEAD','OPTIONS'].includes(init.method.toUpperCase()))headers.set('X-Admin-CSRF',csrf)
 if(init?.body&&!(init.body instanceof FormData))headers.set('Content-Type','application/json')
 const resp=await fetch(path,{...init,headers,credentials:'same-origin',cache:'no-store'})
 if(!resp.ok){
  if(resp.status===401&&!operatorToken){clearOperatorAuth();window.dispatchEvent(new Event('autocodex-admin-auth-expired'))}
  throw await parseApiError(resp)
 }
 if(resp.status===204)return undefined as T
 return resp.json() as Promise<T>
}
export const qs=(params:Record<string,string|number|boolean|undefined>)=>{const s=new URLSearchParams();for(const [k,v] of Object.entries(params))if(v!==undefined&&v!=='')s.set(k,String(v));return s.toString()}
export const WORKSPACE='/api/v1/backoffice/workspace'
export const MONITOR='/api/v1/backoffice/monitor'
export const OPS='/api/v1/backoffice/operations'
export const CATALOG_EDITOR='/api/v1/backoffice/catalog-editor'
