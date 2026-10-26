import {Link} from 'react-router-dom'
import {useQuery} from '@tanstack/react-query'
import {api,isStaffOwner} from '@/lib/api'
import {ShieldCheck,ShieldAlert,CodeXml,ClipboardCheck} from 'lucide-react'
import {Badge} from '@/components/ui/badge'
import {Card,CardContent,CardHeader,CardTitle} from '@/components/ui/card'
import {ErrorBox,Heading,Loading} from '@/components/page'

type RouteAudit={protected_routes:number;classified_routes:number;unclassified:{path:string;methods:string[]}[];unexpectedly_public:{path:string;methods:string[]}[];authentication_exceptions:string[];policy_complete:boolean;functional_parity_verified:boolean}
type Milestone={area:string;route:string;status:'implemented'|'partial'|'blocked';test:string}
const coverage:Milestone[]=[
 {area:'Заказы покупателей: списки, статусы, события',route:'/orders',status:'implemented',test:'Проверить API и данные в Docker'},
 {area:'Предложение замены клиенту',route:'/orders',status:'partial',test:'Проверить 409/422 и согласование клиента'},
 {area:'Точный просмотр попытки и ручная сверка v0.12',route:'/orders?tab=purchases',status:'partial',test:'Read-only API, точный UUID, проверка приватности и сопоставление с заказом — протестированы; нужны настоящие браузер и PostgreSQL'},
 {area:'Права на предложения замены и детали заказа v0.12',route:'/orders',status:'partial',test:'Исправлен scope orders, просмотр журнала закупок выделен в purchases. Проверить личные роли в Docker'},
 {area:'Диагностика попыток закупки по конкретному заказу v0.11',route:'/orders',status:'partial',test:'Фильтр order_id проверен HTTP-тестами; требуется браузерный запуск и сверка с реальной базой'},
 {area:'Реальные закупки поставщикам',route:'/orders?tab=purchases',status:'blocked',test:'Требуется безопасный полный тест без реальной закупки'},
 {area:'Возвраты и платёжная сверка',route:'/orders?tab=refunds',status:'partial',test:'Нет подтверждённого реального банковского контура'},
 {area:'Товар, SEO, характеристики, категории',route:'/catalog',status:'implemented',test:'Проверить publish gate и конкурентные изменения'},
 {area:'Публикация и снятие товара v0.11',route:'/catalog',status:'partial',test:'Явное подтверждение и backend gate; проверить реальные ограничения и E2E'},
 {area:'Бренды, профили, алиасы',route:'/catalog/brands',status:'partial',test:'Проверить конфликты алиасов на реальной БД'},
 {area:'Медиа, модерация и защищённый предпросмотр v0.11',route:'/catalog',status:'partial',test:'Предпросмотр через авторизованный Blob-запрос; нужна проверка браузеров и форматов'},
 {area:'Семейства и вариации',route:'/catalog/families',status:'partial',test:'Проверить ограничения уникальности и циклы'},
 {area:'Комплекты и компоненты',route:'/catalog',status:'partial',test:'Проверить ограничения published/assembled_plan'},
 {area:'Поставщики и API-аккаунты',route:'/suppliers',status:'implemented',test:'Проверить логирование и лимиты API'},
 {area:'Курьерские маршруты и приёмка',route:'/suppliers/control',status:'partial',test:'Нельзя подменять курьерский маршрут самовывозом'},
 {area:'Прайсы, версии, сопоставления',route:'/suppliers/feeds',status:'partial',test:'Проверить staged/rollback/sync'},
 {area:'Свои склады и остатки',route:'/warehouses',status:'implemented',test:'Проверить ledger для физического остатка'},
 {area:'Продавцы, магазины, б/у детали',route:'/marketplace',status:'implemented',test:'Проверить организационные и ценовые ограничения'},
 {area:'Юридические лица и налоговые профили',route:'/finance/studio',status:'partial',test:'Документы и подтверждения должны быть реальными'},
 {area:'F2/F3/F4/F5 SHADOW',route:'/finance/studio',status:'partial',test:'Только симуляции; никакого банка'},
 {area:'Аналитика',route:'/analytics',status:'partial',test:'Проверить семантику показателей и даты'},
 {area:'Фоновые задания и журнал',route:'/system',status:'partial',test:'Проверить пагинацию и детальные ошибки'},
 {area:'Личные аккаунты сотрудников, серверный RBAC и CSRF',route:'/system/staff',status:'partial',test:'API и интеграционные тесты реализованы; нужна проверка с реальной PostgreSQL, HTTPS и выключенным legacy-токеном'},
 {area:'Изолированные сессии сотрудников и отзыв доступов v0.9',route:'/system/sessions',status:'partial',test:'Серверные SQLite тесты прошли; требуется проверка Alembic на PostgreSQL и в браузере'},
 {area:'Клиентские ограничения, основанные на серверных правах',route:'/system/staff',status:'partial',test:'Меню и основные формы связаны с capabilities сервера; специализированные экраны требуют ручного UX-аудита'},
 {area:'E2E тесты и снятие /operator',route:'/system',status:'blocked',test:'Playwright smoke-тесты написаны, но без настоящего браузерного прогона и тестовой БД /operator удалять нельзя'},
]
export function MigrationAudit(){const done=coverage.filter(x=>x.status==='implemented').length;const partial=coverage.filter(x=>x.status==='partial').length;const blocked=coverage.filter(x=>x.status==='blocked').length
 const owner=isStaffOwner()
 const inventory=useQuery({queryKey:['admin','route-audit'],queryFn:()=>api<RouteAudit>('/api/v1/admin/staff/route-audit'),enabled:owner,staleTime:60_000})
 return <><Heading title="Приёмка админки" description="Честная матрица соответствия между старым /operator и новой административной системой. Кодовые экраны не равны проверенному функциональному паритету."/><div className="mb-6 grid gap-3 sm:grid-cols-3">{[{name:'Реализованные экраны',value:done,icon:ClipboardCheck},{name:'Нужна интеграционная проверка',value:partial,icon:CodeXml},{name:'Блокирующие недоработки',value:blocked,icon:ShieldAlert}].map(({name,value,icon:Icon})=><Card key={name} className="p-5"><div className="flex justify-between"><p className="text-xs text-muted-foreground">{name}</p><Icon size={18}/></div><p className="mt-3 text-3xl font-semibold">{value}</p></Card>)}</div><Card className="mb-6"><CardHeader><CardTitle>Серверная проверка защищённых маршрутов</CardTitle></CardHeader><CardContent className="space-y-3">
  <p className="text-sm text-muted-foreground">Автоматическая проверка конфигурации FastAPI в запущенном приложении. Это контроль RBAC, а не доказательство переноса всех функций /operator.</p>
  {!owner?<p className="text-sm text-muted-foreground">Сведения о защищённых маршрутах доступны владельцу.</p>:inventory.isPending?<Loading/>:inventory.error?<ErrorBox error={inventory.error} onRetry={()=>void inventory.refetch()}/>:inventory.data&&<>
   <div className="flex flex-wrap items-center gap-3"><Badge variant={inventory.data.policy_complete?'success':'danger'}>{inventory.data.policy_complete?'Политика покрывает маршруты':'Обнаружены пробелы в политике'}</Badge><span className="text-sm">{inventory.data.classified_routes} / {inventory.data.protected_routes} защищённых маршрутов классифицированы</span></div>
   {inventory.data.unclassified.concat(inventory.data.unexpectedly_public).length>0&&<div role="alert" className="space-y-1 rounded-xl border border-destructive/40 p-3 text-sm"><strong>Требуется исправление доступа:</strong>{inventory.data.unclassified.map(r=><p key={r.path}>Нет правила: <code>{r.path}</code></p>)}{inventory.data.unexpectedly_public.map(r=><p key={r.path}>Нет защиты: <code>{r.path}</code></p>)}</div>}
   <p className="text-xs text-muted-foreground">Три маршрута авторизации (вход, сеанс, выход) работают по отдельной проверке. Функциональный паритет всё ещё требует независимой приёмки.</p>
  </>}
 </CardContent></Card><Card><CardHeader><CardTitle>Функциональное покрытие</CardTitle></CardHeader><CardContent className="space-y-2">{coverage.map(item=><div key={item.area} className="flex flex-wrap items-start justify-between gap-4 border-b border-border py-3 last:border-0"><div className="flex-1"><Link to={item.route} className="text-sm font-medium hover:underline">{item.area}</Link><p className="mt-1 text-xs text-muted-foreground">{item.test}</p></div><Badge variant={item.status==='implemented'?'success':item.status==='blocked'?'danger':'warning'}>{item.status==='implemented'?'Код готов':item.status==='partial'?'Проверить':'Не завершено'}</Badge></div>)}</CardContent></Card><Card className="mt-5"><CardContent className="flex items-start gap-3 p-5"><ShieldCheck size={20} className="shrink-0"/><div><h3 className="font-semibold">Когда можно отключить старую панель?</h3><p className="mt-2 text-sm text-muted-foreground">После проверки RBAC в PostgreSQL и браузере, отключения аварийного общего токена, полного сопоставления операций и успешных E2E-тестов на тестовой БД. Пока маршрут /operator сохраняется как резервный. Общий X-Admin-Token остаётся обходным аварийным доступом, пока явно не выключен на сервере.</p></div></CardContent></Card></>
}
