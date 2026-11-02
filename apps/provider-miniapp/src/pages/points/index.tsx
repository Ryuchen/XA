import {useEffect,useState} from 'react';
import {Button,Input,Text,View} from '@tarojs/components';
import Taro,{usePullDownRefresh} from '@tarojs/taro';
import PlatformLayout from '@/components/PlatformLayout';
import PageNav from '@/components/PageNav';
import {Binding,Claim,PointRow,Reward,createBinding,fetchBinding,fetchClaim,fetchClaims,fetchPoints,fetchRewards,redeemPoints,saveClaimDetails} from '@/services/points';
import {getSessionRevision,getStoredUser} from '@/utils/auth';
import styles from './index.module.scss';

type PendingRedemption={rewardId:number;name:string;requestId:string;details:{game_uid:string;note:string}};
const detailFields:Record<string,Array<{key:string;placeholder:string;max:number}>>={
  TAG:[{key:'tag',placeholder:'希望展示的 TAG',max:30}],
  NAMING:[{key:'provider_name',placeholder:'希望冠名的陪玩',max:50}],
  PHYSICAL:[{key:'recipient',placeholder:'收件人',max:50},{key:'phone',placeholder:'联系电话',max:20},{key:'address',placeholder:'完整收货地址',max:255}],
};

export default function PointsPage(){
  const [focusedId,setFocusedId]=useState<number|null>(()=>{const raw=Taro.getCurrentInstance().router?.params?.claim_id||'';return /^[1-9]\d*$/.test(raw)&&Number.isSafeInteger(Number(raw))?Number(raw):null;});
  const [focusedClaim,setFocusedClaim]=useState<Claim|null>(null);
  const [points,setPoints]=useState(0),[ledger,setLedger]=useState<PointRow[]>([]),[next,setNext]=useState<number|null>(null);
  const [binding,setBinding]=useState<Binding|null>(null),[code,setCode]=useState(''),[rewards,setRewards]=useState<Reward[]>([]),[claims,setClaims]=useState<Claim[]>([]),[claimsNext,setClaimsNext]=useState<number|null>(null);
  const [tab,setTab]=useState(focusedId?'claims':'shop'),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const [shopUid,setShopUid]=useState(''),[shopNote,setShopNote]=useState('');
  const [uid,setUid]=useState(''),[note,setNote]=useState(''),[editing,setEditing]=useState<Claim|null>(null);
  const [shopExtra,setShopExtra]=useState<Record<string,string>>({}),[editExtra,setEditExtra]=useState<Record<string,string>>({});
  const [editIssue,setEditIssue]=useState('');
  const [owner,setOwner]=useState(()=>getStoredUser()?.id);
  const pendingKey=`xa-points-redemption:${owner}`;
  const [pendingRedemption,setPendingRedemption]=useState<PendingRedemption|null>(()=>{
    try{return owner?Taro.getStorageSync(pendingKey)||null:null;}catch{return null;}
  });
  useEffect(()=>{
    const changed=()=>{const next=getStoredUser()?.id;if(next===owner)return;setOwner(next);try{setPendingRedemption(next?Taro.getStorageSync(`xa-points-redemption:${next}`)||null:null);}catch{setPendingRedemption(null);}};
    Taro.eventCenter.on('xa:auth.changed',changed);return()=>{Taro.eventCenter.off('xa:auth.changed',changed);};
  },[owner]);
  const clearToast=()=>{try{void Promise.resolve(Taro.hideToast()).catch(()=>{});}catch{/* optional platform feedback */}};
  const notify=(e:unknown)=>Taro.showToast({title:e instanceof Error?e.message:'操作失败',icon:'none'});
  const data=<T,>(r:{code:number;data?:T;msg?:string}):T=>{if(r.code!==0||r.data===undefined)throw new Error(r.msg||'加载失败');return r.data;};
  async function load(){setLoading(true);setError('');try{
    const [p,b,r,c,focused]=await Promise.all([fetchPoints(),fetchBinding(),fetchRewards(),fetchClaims(),focusedId?fetchClaim(focusedId):Promise.resolve(null)]);
    const overview=data(p),claimPage=data(c);setPoints(overview.points);setLedger(overview.list);setNext(overview.next);setBinding(data(b));setRewards(data(r));setClaims(claimPage.items);setClaimsNext(claimPage.next);
    setFocusedClaim(focused ? data(focused) : null);
  }catch(e){setError(e instanceof Error?e.message:'加载失败');}finally{setLoading(false);}}
  useEffect(()=>{load();},[]);
  usePullDownRefresh(async()=>{await load();Taro.stopPullDownRefresh();});
  async function bind(){if(busy)return;setBusy(true);try{const r=data(await createBinding());setCode(r.code);await Taro.setClipboardData({data:'绑定 '+r.code});}catch(e){notify(e);}finally{setBusy(false);}}
  async function submitRedemption(pending:PendingRedemption){
    const revision=getSessionRevision();
    try{
      const result=await redeemPoints(pending.rewardId,pending.details,pending.requestId);
      if(revision!==getSessionRevision())return;
      if(!result||typeof result.code!=='number'||result.code>=500||result.code===409||result.code===429){
        Taro.showToast({title:'兑换结果未确认，请重试原申请',icon:'none'});return;
      }
      Taro.removeStorageSync(pendingKey);setPendingRedemption(null);
      if(result.code!==0){notify(new Error(result.msg||'兑换未成功'));return;}
      setTab('claims');await load();Taro.showToast({title:'兑换已提交',icon:'success'});
    }catch(e){if(revision===getSessionRevision())Taro.showToast({title:'兑换结果未确认，请重试原申请',icon:'none'});}
  }
  async function redeem(reward:Reward){if(busy||pendingRedemption)return;const revision=getSessionRevision();setBusy(true);try{
    const answer=await Taro.showModal({title:'确认兑换',content:`使用${reward.point_cost}积分兑换「${reward.name}」，交付由客服办理。`});if(!answer.confirm)return;
    if(revision!==getSessionRevision()||getStoredUser()?.id!==owner)throw new Error('登录状态已变化，请重新操作');
    const extra=Object.fromEntries((detailFields[reward.kind]||[]).map(field=>[field.key,(shopExtra[field.key]||'').trim()]));
    const pending={rewardId:reward.id,name:reward.name,requestId:`redeem-${Date.now()}-${Math.random().toString(36).slice(2)}`,details:{...extra,game_uid:['SERVICE','VIRTUAL'].includes(reward.kind)?shopUid.trim():'',note:shopNote.trim()}};
    if(!owner)throw new Error('请先登录');
    Taro.setStorageSync(pendingKey,pending);setPendingRedemption(pending);
    await submitRedemption(pending);
  }catch(e){notify(e);}finally{setBusy(false);}}
  async function retryRedemption(){if(busy||!pendingRedemption)return;setBusy(true);try{await submitRedemption(pendingRedemption);}finally{setBusy(false);}}
  async function more(){if(!next||busy)return;setBusy(true);try{const r=data(await fetchPoints(next));setLedger(prev=>[...prev,...r.list]);setNext(r.next);}catch(e){notify(e);}finally{setBusy(false);}}
  async function moreClaims(){if(!claimsNext||busy)return;setBusy(true);try{const r=data(await fetchClaims(claimsNext));setClaims(prev=>[...prev,...r.items.filter(item=>!prev.some(old=>old.id===item.id))]);setClaimsNext(r.next);}catch(e){notify(e);}finally{setBusy(false);}}
  async function refreshEditing(){if(!editing||busy)return;setBusy(true);try{
    const latest=data(await fetchClaim(editing.id));setClaims(previous=>previous.map(claim=>claim.id===latest.id?latest:claim));
    setFocusedClaim(previous=>previous?.id===latest.id?latest:previous);setEditing(latest);
    if(latest.status==='PENDING'||latest.tag_editable){setEditIssue('');Taro.showToast({title:'可继续保存资料',icon:'none'});}
    else Taro.showToast({title:'客服尚未退回，请稍后再查',icon:'none'});
  }catch(e){notify(e);}finally{setBusy(false);}}
  async function save(){if(!editing||busy||editIssue)return;setBusy(true);try{const response=await saveClaimDetails(editing.id,{...editing.details,...Object.fromEntries((detailFields[editing.kind_snapshot]||[]).map(field=>[field.key,(editExtra[field.key]||'').trim()])),...(['SERVICE','VIRTUAL'].includes(editing.kind_snapshot)?{game_uid:uid.trim()}:{}),note:note.trim()});if(response.errorCode==='CLAIM_NOT_EDITABLE'){
    clearToast();
    setEditIssue('权益状态已变化，刚填写的资料尚未保存。可复制后联系客服。');
    try{const latest=data(await fetchClaim(editing.id));setClaims(previous=>previous.map(claim=>claim.id===latest.id?latest:claim));setFocusedClaim(previous=>previous?.id===latest.id?latest:previous);setEditing(latest);}catch{ /* 保留草稿供复制 */ }
    return;
  }const updated=data(response);setClaims(previous=>previous.map(claim=>claim.id===updated.id?updated:claim));setFocusedClaim(previous=>previous?.id===updated.id?updated:previous);setEditing(null);Taro.showToast({title:editing.tag_editable?(updated.tag_revision_submitted?'已提交复核':'内容未改变'):'交付资料已保存',icon:'success'});}catch(e){notify(e);}finally{setBusy(false);}}
  return <PlatformLayout active='mine'><View className={styles.container}><PageNav title='我的积分'/>
    <View className={styles.overview}><Text className={styles.caption}>可用积分</Text><Text className={styles.total}>{points}</Text><Button className={styles.secondary} onClick={load} disabled={busy||loading}>刷新</Button></View>
    {!!error&&<View className={styles.notice}><Text>{error}</Text><Button onClick={load}>重新加载</Button></View>}
    {!!pendingRedemption&&<View className={styles.notice}><Text>「{pendingRedemption.name}」兑换结果未确认，请重试原申请；不会重复扣除积分。</Text><Button disabled={busy} onClick={retryRedemption}>重试原申请</Button></View>}
    {binding&&<View className={styles.card}><Text className={styles.title}>KOOK积分</Text><Text className={styles.description}>{binding.rules}</Text>
      <Text className={styles.description}>{binding.connected?'自动计分已连接':'自动计分连接中'} · {binding.bound?`已绑定${binding.nickname||'本人账号'}`:'尚未绑定'}</Text>
      {binding.bound?<><Text className={styles.description}>累计在线{Math.floor(binding.online_seconds/3600)}小时{Math.floor(binding.online_seconds%3600/60)}分钟 · 在线获得{binding.online_points}积分</Text><Text className={styles.description}>{binding.checked_in?'今日已签到':'请在KOOK签到大厅发送“签到”，每天获得5积分。'}</Text></>:<><Button className={styles.primary} disabled={busy||!binding.enabled} onClick={bind}>获取并复制绑定码</Button>{!!code&&<Text className={styles.description}>请用本人KOOK账号在签到大厅发送“绑定 {code}”。验证码10分钟有效，发送后点击刷新。</Text>}</>}
    </View>}
    <View className={styles.tabs}>{[['shop','积分兑换'],['claims','兑换记录'],['ledger','积分明细']].map(([key,label])=><Button key={key} className={tab===key?styles.primary:styles.secondary} onClick={()=>{setFocusedId(null);setFocusedClaim(null);setTab(key);}}>{label}</Button>)}</View>
    {!loading&&!error&&tab==='claims'&&!!focusedId&&<View className={styles.card}><Text className={styles.description}>正在查看通知关联的权益</Text><Button className={styles.secondary} onClick={()=>{setFocusedId(null);setFocusedClaim(null);}}>查看全部记录</Button></View>}
    {!loading&&!error&&tab==='shop'&&<><View className={styles.card}><Text className={styles.title}>账号交付资料</Text>{rewards.some(r=>['SERVICE','VIRTUAL'].includes(r.kind))&&<Input className={styles.input} value={shopUid} placeholder='游戏UID（可在兑换后补充）' maxlength={80} onInput={e=>setShopUid(e.detail.value)}/>}{rewards.some(r=>r.kind==='TAG')&&<Text className={styles.description}>TAG 由客服审核，通过后最多展示最近 3 个。</Text>}{Array.from(new Set(rewards.map(r=>r.kind))).flatMap(kind=>(detailFields[kind]||[]).map(field=><Input key={kind+field.key} className={styles.input} value={shopExtra[field.key]||''} placeholder={field.placeholder} maxlength={field.max} onInput={e=>setShopExtra(previous=>({...previous,[field.key]:e.detail.value}))}/>))}<Input className={styles.input} value={shopNote} placeholder='交付要求（可选）' maxlength={255} onInput={e=>setShopNote(e.detail.value)}/></View><View className={styles.grid}>{rewards.map(r=><View key={r.id} className={styles.card}><Text className={styles.title}>{r.name}</Text><Text className={styles.description}>{r.description}</Text><Text className={styles.cost}>{r.point_cost}积分</Text><Button className={styles.primary} disabled={busy||!!pendingRedemption||points<r.point_cost||r.stock===0} onClick={()=>redeem(r)}>{r.stock===0?'已兑完':points<r.point_cost?'积分不足':'兑换'}</Button></View>)}</View>{!rewards.length&&<Text className={styles.description}>暂无可兑换项目</Text>}</>}
    {!loading&&!error&&tab==='claims'&&(focusedId?(focusedClaim?[focusedClaim]:[]):claims).map(c=><View key={c.id} className={styles.card}><Text className={styles.title}>{c.name_snapshot}</Text><Text className={styles.description}>{c.status_label}{c.delivery_note?' · '+c.delivery_note:''}</Text>{c.kind_snapshot==='TAG'&&c.status==='PENDING'&&c.tag_revision_required&&<Text className={styles.description}>请按客服要求修改 TAG 后保存。</Text>}{c.kind_snapshot==='TAG'&&c.status==='COMPLETED'&&c.tag_reviewed===false&&<Text className={styles.description}>{c.tag_suspension_reason?'标签已暂停展示：'+c.tag_suspension_reason:'标签待客服复核，暂不展示。'}</Text>}{c.points_returned&&<Text className={styles.description}>已退回 {c.points_spent} 积分，可在积分明细查看。</Text>}{['SERVICE','VIRTUAL'].includes(c.kind_snapshot)&&<Text className={styles.description}>游戏UID：{c.details.game_uid||'待补充'}</Text>}{(detailFields[c.kind_snapshot]||[]).map(field=><Text key={field.key} className={styles.description}>{field.placeholder}：{c.details[field.key]||'待补充'}</Text>)}{c.tag_suspension_reason&&c.expires_at&&<Text className={styles.description}>{new Date(c.expires_at).getTime()<=Date.now()?'已到期':'有效至 '+new Date(c.expires_at).toLocaleString()}</Text>}{c.tag_editable&&<Text className={styles.description}>{c.tag_revision_submitted?'已提交修改，等待客服复核。':'可修改后提交复核，有效期不变。'}</Text>}{(c.status==='PENDING'||c.tag_editable)&&<Button className={styles.secondary} disabled={!!editing||busy} onClick={()=>{clearToast();setEditIssue('');setEditing(c);setEditExtra(c.details);setUid(c.details.game_uid||'');setNote(c.details.note||'');}}>{c.tag_editable?'修改 TAG':'补充交付资料'}</Button>}</View>)}
    {!loading&&!error&&tab==='claims'&&!focusedId&&!!claimsNext&&<Button className={styles.secondary} disabled={busy} onClick={moreClaims}>加载更多记录</Button>}
    {tab==='claims'&&!!editing&&<View className={styles.card}><Text className={styles.title}>补充资料 · {editing.name_snapshot}</Text>{!!editIssue&&<Text className={`${styles.description} ${styles.feedback}`}>{editIssue}</Text>}{['SERVICE','VIRTUAL'].includes(editing.kind_snapshot)&&<Input className={styles.input} value={uid} placeholder='游戏UID' maxlength={80} onInput={e=>setUid(e.detail.value)}/>}{(detailFields[editing.kind_snapshot]||[]).map(field=><Input key={field.key} className={styles.input} value={editExtra[field.key]||''} placeholder={field.placeholder} maxlength={field.max} onInput={e=>setEditExtra(previous=>({...previous,[field.key]:e.detail.value}))}/>)}{!editing.tag_editable&&<Input className={styles.input} value={note} placeholder='交付说明' maxlength={255} onInput={e=>setNote(e.detail.value)}/>}{!editIssue?<Button className={styles.primary} disabled={busy} onClick={save}>{editing.tag_editable?'提交复核':'保存资料'}</Button>:<><Button className={styles.secondary} disabled={busy} onClick={refreshEditing}>刷新交付状态</Button><Button className={styles.secondary} onClick={()=>Taro.setClipboardData({data:[uid.trim()&&`游戏UID：${uid.trim()}`,...(detailFields[editing.kind_snapshot]||[]).map(field=>editExtra[field.key]?.trim()&&`${field.placeholder}：${editExtra[field.key].trim()}`),note.trim()&&`交付说明：${note.trim()}`].filter(Boolean).join('\n')})}>复制未保存资料</Button><Button className={styles.secondary} onClick={()=>Taro.navigateTo({url:'/pages/support-chat/index'})}>联系客服</Button></>}<Button className={styles.secondary} disabled={busy} onClick={()=>{setEditing(null);setEditIssue('');}}>关闭</Button></View>}
    {!loading&&!error&&tab==='ledger'&&<><View className={styles.card}>{ledger.map(r=><View key={r.id} className={styles.row}><View><Text>{r.remark}</Text><Text className={styles.caption}>{r.created_at.slice(0,16).replace('T',' ')}</Text></View><Text className={styles.cost}>{r.amount>0?'+':''}{r.amount}</Text></View>)}{!ledger.length&&<Text>暂无积分明细</Text>}</View>{next&&<Button className={styles.secondary} disabled={busy} onClick={more}>加载更多</Button>}</>}
  </View></PlatformLayout>;
}
