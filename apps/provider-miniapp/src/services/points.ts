import { ApiResponse, request } from '@/utils/request';
export interface PointRow { id:number; amount:number; balance_after:number; remark:string; created_at:string; }
export interface Points { points:number; list:PointRow[]; next:number|null; }
export interface Binding { enabled:boolean; connected:boolean; bound:boolean; nickname:string; online_seconds:number; online_points:number; checked_in:boolean; rules:string; }
export interface Reward { id:number; name:string; kind:string; point_cost:number; description:string; stock:number|null; }
export interface Claim { id:number; name_snapshot:string; kind_snapshot:string; tag_reviewed:boolean|null; tag_revision_required:boolean; tag_editable:boolean; tag_revision_submitted:boolean; expires_at:string|null; tag_suspension_reason:string; source:string; points_spent:number; points_returned:boolean; status:string; status_label:string; delivery_note:string; details:Record<string,string>; }
export const fetchPoints=(before?:number)=>request<ApiResponse<Points>>('/club/points/'+(before?'?before='+before:''),'GET');
export const fetchBinding=()=>request<ApiResponse<Binding>>('/club/kook-binding/','GET');
export const createBinding=()=>request<ApiResponse<{code:string}>>('/club/kook-binding/','POST',{});
export const fetchRewards=()=>request<ApiResponse<Reward[]>>('/club/rewards/','GET');
export const fetchClaims=(before?:number)=>request<ApiResponse<{items:Claim[];next:number|null}>>('/club/claims/?page_size=20'+(before?'&before='+before:''),'GET');
export const fetchClaim=(id:number)=>request<ApiResponse<Claim>>(`/club/claims/${id}/`,'GET');
export const redeemPoints=(id:number,details:Record<string,string>,requestId:string)=>request<ApiResponse<Claim>>('/club/redeem/','POST',{reward_id:id,details,client_request_id:requestId});
export const saveClaimDetails=(id:number,details:Record<string,string>)=>request<ApiResponse<Claim>>(`/club/claims/${id}/`,'PATCH',{details});
