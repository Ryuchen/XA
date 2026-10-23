Component({
  properties: { loading: Boolean, error: String, empty: Boolean, emptyText: { type: String, value: '暂无内容' } },
  methods: { retry() { this.triggerEvent('retry'); } },
});
