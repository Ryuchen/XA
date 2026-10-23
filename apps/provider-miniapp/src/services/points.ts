import { ApiResponse, request } from '@/utils/request';
import { fundRequest } from '@/utils/fundRequest';
export interface PointRow { id:number; amount:number; balance_after:number; remark:string; created_at:string; }
export interface Points { points:number; list:PointRow[]; next:number|null; }
export interface Binding { enabled:boolean; connected:boolean; bound:boolean; nickname:string; online_seconds:number; online_points:number; checked_in:boolean; rules:string; }
export interface Reward { id:number; name:string; point_cost:number; description:string; stock:number|null; }
export interface Claim { id:number; name_snapshot:string; status:string; status_label:string; delivery_note:string; details:Record<string,string>; }
export const fetchPoints=(before?:number)=>request<ApiResponse<Points>>('/club/points/'+(before?'?before='+before:''),'GET');
export const fetchBinding=()=>request<ApiResponse<Binding>>('/club/kook-binding/','GET');
export const createBinding=()=>request<ApiResponse<{code:string}>>('/club/kook-binding/','POST',{});
export const fetchRewards=()=>request<ApiResponse<Reward[]>>('/club/rewards/','GET');
export const fetchClaims=()=>request<ApiResponse<Claim[]>>('/club/claims/','GET');
export const redeemPoints=(id:number,details:Record<string,string>)=>fundRequest<Claim>('/club/redeem/',{reward_id:id,details});
export const saveClaimDetails=(id:number,details:Record<string,string>)=>request<ApiResponse<Claim>>(`/club/claims/${id}/`,'PATCH',{details});
