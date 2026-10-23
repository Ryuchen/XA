const { privatePage } = require('../../core/page');
const { request, upload } = require('../../core/api');
const auth = require('../../core/auth');
const { toast, go } = require('../../core/ui');
Page(privatePage({
  data: { nickname: '', phone: '', avatar: '', profiles: [], selected: 0, busy: false },
  async fetch() {
    const [me, categories] = await Promise.all([request('/users/me/'), request('/orders/game-categories/')]);
    const profiles = categories.map(category => ({ game_category: category.id, name: category.name, region: '', nickname: '', uid: '', is_default: false, ...(me.game_profiles || []).find(item => item.game_category === category.id) }));
    return { nickname: me.nickname, phone: me.phone, avatar: me.avatar || '', profiles, selected: Math.max(0, profiles.findIndex(item => item.is_default)) };
  },
  input(event) { this.setData({ [event.currentTarget.dataset.field]: event.detail.value }); },
  gameInput(event) { this.setData({ ['profiles[' + this.data.selected + '].' + event.currentTarget.dataset.field]: event.detail.value }); },
  choose(event) { this.setData({ selected: Number(event.detail.value) }); },
  security() { go('/pages/security/index'); },
  async chooseAvatar(event) {
    const file = event.detail.avatarUrl;
    if (!file || this.data.busy || !auth.ensure()) return;
    this.setData({ busy: true });
    try {
      const result = await upload('/users/me/avatar/', file, 'avatar');
      const session = auth.getSession();
      if (this._alive && session) {
        auth.updateUser({ avatar: result.avatar });
        this.setData({ avatar: result.avatar });
        toast('头像已更新');
      }
    } catch (error) { if (this._alive) toast(error); }
    finally { if (this._alive) this.setData({ busy: false }); }
  },
  async save() {
    if (this.data.busy || !auth.ensure() || this.data.loading || this.data.error) return;
    const nickname = this.data.nickname.trim();
    if (!nickname) { toast('请填写昵称'); return; }
    this.setData({ busy: true });
    try {
      const profiles = this.data.profiles.map((item, index) => ({ game_category: item.game_category, region: item.region.trim(), nickname: item.nickname.trim(), uid: item.uid.trim(), is_default: index === this.data.selected }));
      const me = await request('/users/me/', 'PATCH', { nickname, game_profiles: profiles });
      const session = auth.getSession();
      if (this._alive && session) { auth.setSession({ ...session, user: { ...session.user, ...me } }); toast('资料已保存，下单时自动回填'); }
    } catch (error) { if (this._alive) toast(error); }
    finally { if (this._alive) this.setData({ busy: false }); }
  },
}));
