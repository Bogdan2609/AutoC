export type Page<T> = { total: number; page: number; size: number; items: T[] }
export type Summary = {
  products:number; merchants:number; stores:number; nodes:number; offers:number;
  supplier_accounts:number; orders:number; used_parts:number; routes:number;
  review_products:number; active_offers:number; api_errors:number; cache_errors:number
}
export type Attention = {
  review_orders:number; review_purchases:number; review_refunds:number; failed_jobs:number;
  overdue_queued_jobs:number; note:string
}
export type Configuration = {
  environment:string; supplier_api_enabled:boolean; supplier_procurement_enabled:boolean;
  real_supplier_checkout_enabled:boolean; sbp_credentials_configured:boolean;
  financial_operations_verified:boolean; warning:string
}
export type Product = {id:string;brand:string;article:string;name:string;status:string;slug:string;created_at:string}
export type Order = {id:string;status:string;total:string;total_is_final:boolean;created_at:string}
export type OrderEvent = {id:string;type:string;actor:string;created_at:string;line_id:string|null}
export type OrderDetail = {
  id:string;total:string;total_is_final:boolean;status:string;
  items:{id:string;title:string;supplier:string;quantity:number;retail:string;purchase:string;status:string;
    procurement:{status:string;external_order_id:string|null;error:string|null}|null;
    replacements:{id:string;status:string;alternative_id:string|null}[]}[];
  payments:{id:string;status:string;amount:string;provider_id:string|null}[]
}
export type ProductIdentity = {
  id:string;name:string;description:string|null;slug:string;brand:string;article:string;
  seo_title:string|null;seo_description:string|null;seo_h1:string|null;seo_noindex:boolean;
  updated_at:string|null;status:string;attributes:{code:string;value:string|number|boolean|null}[];
  categories:{category_id:string;is_primary:boolean}[];missing_required:unknown[]
}
export type Supplier = {id:string;name:string;code:string}
export type Account = {id:string;label:string;supplier_code:string;supplier:string;merchant:string;enabled:boolean;read_enabled:boolean;procurement_enabled:boolean;requests_per_hour:number;credential_profile:string}
export type Destination = {id:string;account_id:string;account:string;node:string;city:string;external_delivery_id:string|null;enabled:boolean;verified:boolean}
export type ReadyItem = {destination_id:string;account_id:string;account:string;supplier:string;node:string;city:string;status:string;issues:string[];contact_configured:boolean;requisite_bound:boolean}
export type Readiness = {items:ReadyItem[];ready:number;action_required:number;unmapped_accounts:{id:string;label:string;supplier:string}[];note:string;truncated:boolean}
