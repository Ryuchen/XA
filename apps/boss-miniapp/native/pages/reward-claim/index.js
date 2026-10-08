const { request } = require('../../core/api'); const auth = require('../../core/auth'); const { on } = require('../../core/events'); const { go, toast } = require('../../core/ui');
Page({
 data:{loading:false,error:'',claim:null,busy:false,details:{}},
 onLoad(options){this._id=Number(options.id);this._alive=true;this._seq=0;this._off=on('session.changed',()=>{this._seq++;this.setData({claim:null,details:{}});if(this._visible)this.load();});},
 onShow(){this._visible=true;this.load();},onHide(){this._visible=false;},onUnload(){this._alive=false;this._off();},async onPullDownRefresh(){await this.load();wx.stopPullDownRefresh();},
 async load(){const seq=++this._seq;if(!auth.ensure())return;this.setData({loading:true,error:''});try{const claim=await request('/club/claims/'+this._id+'/');if(!this._alive||seq!==this._seq)return;if(!claim)throw new Error('未找到本人奖品记录');this.setData({claim,details:claim.details||{},loading:false});}catch(e){if(this._alive&&seq===this._seq)this.setData({loading:false,error:e.message});}},
 input(event){this.setData({['details.'+event.currentTarget.dataset.field]:event.detail.value});},
 async save(){if(this.data.busy)return;const seq=this._seq;this.setData({busy:true});try{const claim=await request('/club/claims/'+this._id+'/','PATCH',{details:this.data.details});if(this._alive&&seq===this._seq){this.setData({claim});toast('交付资料已保存');}}catch(e){if(this._alive&&seq===this._seq)toast(e);}finally{if(this._alive&&seq===this._seq)this.setData({busy:false});}},
 support(){go('/pages/support/index');}
});
