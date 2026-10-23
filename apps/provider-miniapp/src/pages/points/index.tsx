import {useEffect,useState} from 'react';
import {Button,Input,Text,View} from '@tarojs/components';
import Taro,{usePullDownRefresh} from '@tarojs/taro';
import PlatformLayout from '@/components/PlatformLayout';
import PageNav from '@/components/PageNav';
import {Binding,Claim,PointRow,Reward,createBinding,fetchBinding,fetchClaims,fetchPoints,fetchRewards,redeemPoints,saveClaimDetails} from '@/services/points';
import styles from './index.module.scss';

export default function PointsPage(){
  const [points,setPoints]=useState(0),[ledger,setLedger]=useState<PointRow[]>([]),[next,setNext]=useState<number|null>(null);
  const [binding,setBinding]=useState<Binding|null>(null),[code,setCode]=useState(''),[rewards,setRewards]=useState<Reward[]>([]),[claims,setClaims]=useState<Claim[]>([]);
  const [tab,setTab]=useState('shop'),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const [uid,setUid]=useState(''),[note,setNote]=useState(''),[editing,setEditing]=useState<Claim|null>(null);
  const notify=(e:unknown)=>Taro.showToast({title:e instanceof Error?e.message:'操作失败',icon:'none'});
  const data=<T,>(r:{code:number;data?:T;msg?:string}):T=>{if(r.code!==0||r.data===undefined)throw new Error(r.msg||'加载失败');return r.data;};
  async function load(){setLoading(true);setError('');try{
    const [p,b,r,c]=await Promise.all([fetchPoints(),fetchBinding(),fetchRewards(),fetchClaims()]);
    const overview=data(p);setPoints(overview.points);setLedger(overview.list);setNext(overview.next);setBinding(data(b));setRewards(data(r));setClaims(data(c));
  }catch(e){setError(e instanceof Error?e.message:'加载失败');}finally{setLoading(false);}}
  useEffect(()=>{load();},[]);
  usePullDownRefresh(async()=>{await load();Taro.stopPullDownRefresh();});
  async function bind(){if(busy)return;setBusy(true);try{const r=data(await createBinding());setCode(r.code);await Taro.setClipboardData({data:'绑定 '+r.code});}catch(e){notify(e);}finally{setBusy(false);}}
  async function redeem(reward:Reward){if(busy)return;setBusy(true);try{
    const answer=await Taro.showModal({title:'确认兑换',content:`使用${reward.point_cost}积分兑换「${reward.name}」，交付由客服办理。`});if(!answer.confirm)return;
    data(await redeemPoints(reward.id,{game_uid:uid.trim(),note:note.trim()}));setTab('claims');await load();Taro.showToast({title:'兑换已提交',icon:'success'});
  }catch(e){notify(e);}finally{setBusy(false);}}
  async function more(){if(!next||busy)return;setBusy(true);try{const r=data(await fetchPoints(next));setLedger(prev=>[...prev,...r.list]);setNext(r.next);}catch(e){notify(e);}finally{setBusy(false);}}
  async function save(){if(!editing||busy)return;setBusy(true);try{data(await saveClaimDetails(editing.id,{...editing.details,game_uid:uid.trim(),note:note.trim()}));setEditing(null);await load();}catch(e){notify(e);}finally{setBusy(false);}}
  return <PlatformLayout active='mine'><View className={styles.container}><PageNav title='我的积分'/>
    <View className={styles.overview}><Text className={styles.caption}>可用积分</Text><Text className={styles.total}>{points}</Text><Button className={styles.secondary} onClick={load} disabled={busy||loading}>刷新</Button></View>
    {!!error&&<View className={styles.notice}><Text>{error}</Text><Button onClick={load}>重新加载</Button></View>}
    {binding&&<View className={styles.card}><Text className={styles.title}>KOOK积分</Text><Text className={styles.description}>{binding.rules}</Text>
      <Text className={styles.description}>{binding.connected?'自动计分已连接':'自动计分连接中'} · {binding.bound?`已绑定${binding.nickname||'本人账号'}`:'尚未绑定'}</Text>
      {binding.bound?<><Text className={styles.description}>累计在线{Math.floor(binding.online_seconds/3600)}小时{Math.floor(binding.online_seconds%3600/60)}分钟 · 在线获得{binding.online_points}积分</Text><Text className={styles.description}>{binding.checked_in?'今日已签到':'请在KOOK签到大厅发送“签到”，每天获得5积分。'}</Text></>:<><Button className={styles.primary} disabled={busy||!binding.enabled} onClick={bind}>获取并复制绑定码</Button>{!!code&&<Text className={styles.description}>请用本人KOOK账号在签到大厅发送“绑定 {code}”。验证码10分钟有效，发送后点击刷新。</Text>}</>}
    </View>}
    <View className={styles.tabs}>{[['shop','积分兑换'],['claims','兑换记录'],['ledger','积分明细']].map(([key,label])=><Button key={key} className={tab===key?styles.primary:styles.secondary} onClick={()=>setTab(key)}>{label}</Button>)}</View>
    {!loading&&!error&&tab==='shop'&&<><View className={styles.card}><Text className={styles.title}>账号交付资料</Text><Input className={styles.input} value={uid} placeholder='游戏UID（可在兑换后补充）' maxlength={80} onInput={e=>setUid(e.detail.value)}/><Input className={styles.input} value={note} placeholder='交付要求（可选）' maxlength={255} onInput={e=>setNote(e.detail.value)}/></View><View className={styles.grid}>{rewards.map(r=><View key={r.id} className={styles.card}><Text className={styles.title}>{r.name}</Text><Text className={styles.description}>{r.description}</Text><Text className={styles.cost}>{r.point_cost}积分</Text><Button className={styles.primary} disabled={busy||points<r.point_cost||r.stock===0} onClick={()=>redeem(r)}>{r.stock===0?'已兑完':points<r.point_cost?'积分不足':'兑换'}</Button></View>)}</View>{!rewards.length&&<Text className={styles.description}>暂无可兑换项目</Text>}</>}
    {!loading&&!error&&tab==='claims'&&claims.map(c=><View key={c.id} className={styles.card}><Text className={styles.title}>{c.name_snapshot}</Text><Text className={styles.description}>{c.status_label}{c.delivery_note?' · '+c.delivery_note:''}</Text><Text className={styles.description}>游戏UID：{c.details.game_uid||'待补充'}</Text>{c.status==='PENDING'&&<Button className={styles.secondary} onClick={()=>{setEditing(c);setUid(c.details.game_uid||'');setNote(c.details.note||'');}}>补充交付资料</Button>}</View>)}
    {!!editing&&<View className={styles.card}><Text className={styles.title}>补充资料 · {editing.name_snapshot}</Text><Input className={styles.input} value={uid} placeholder='游戏UID' maxlength={80} onInput={e=>setUid(e.detail.value)}/><Input className={styles.input} value={note} placeholder='交付说明' maxlength={255} onInput={e=>setNote(e.detail.value)}/><Button className={styles.primary} disabled={busy} onClick={save}>保存资料</Button><Button className={styles.secondary} disabled={busy} onClick={()=>setEditing(null)}>取消</Button></View>}
    {!loading&&!error&&tab==='ledger'&&<><View className={styles.card}>{ledger.map(r=><View key={r.id} className={styles.row}><View><Text>{r.remark}</Text><Text className={styles.caption}>{r.created_at.slice(0,16).replace('T',' ')}</Text></View><Text className={styles.cost}>{r.amount>0?'+':''}{r.amount}</Text></View>)}{!ledger.length&&<Text>暂无积分明细</Text>}</View>{next&&<Button className={styles.secondary} disabled={busy} onClick={more}>加载更多</Button>}</>}
  </View></PlatformLayout>;
}
