<template>
  <div class="catalog-editor" :class="{ 'is-disabled': disabled }">
    <div class="editor-toolbar" role="toolbar" aria-label="玩法文案格式">
      <button v-for="tool in tools" :key="tool.label" type="button" :title="tool.label" :aria-label="tool.label" :aria-pressed="tool.active?.() || false" :disabled="disabled || !editor" :class="{ active: tool.active?.() }" @click="tool.run()">{{ tool.text }}</button>
      <label class="image-upload" :class="{ busy: uploading || disabled }">
        {{ uploading ? '图片上传中…' : '＋ 上传图片' }}
        <input type="file" accept="image/png,image/jpeg,image/webp" :disabled="uploading || disabled" @change="uploadImage" />
      </label>
      <Button variant="ghost" @click="preview = !preview">{{ preview ? '继续编辑' : '手机预览' }}</Button>
    </div>
    <div v-if="preview" class="preview-wrap"><div class="mobile-preview catalog-document" v-html="editor?.getHTML() || ''" /></div>
    <editor-content v-else :editor="editor" />
    <div class="editor-hint">{{ hint }} 图片支持 PNG、JPEG、WebP，单张不超过 10MB；选中图片可按删除键移除。</div>
    <p v-if="notice" role="status" class="editor-hint">{{ notice }}</p>
  </div>
</template>

<script setup lang="ts">
import { watch, ref, onBeforeUnmount } from 'vue'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import Image from '@tiptap/extension-image'
import { Button } from '@xa/shadcn-vue/button'
import { catalogImageApi } from '@/api/modules'

const props = withDefaults(defineProps<{ modelValue?: string; disabled?: boolean; hint?: string }>(), {
  modelValue: '', disabled: false, hint: '文字、标题、列表和图片会一起显示在小程序的价目下方。',
})
const emit = defineEmits<{ 'update:modelValue': [value: string]; 'uploading': [value: boolean] }>()
const preview = ref(false), uploading = ref(false), notice = ref('')
const CatalogImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      originalSrc: {
        default: null,
        parseHTML: element => element.getAttribute('data-original'),
        renderHTML: attributes => attributes.originalSrc ? { 'data-original': attributes.originalSrc } : {},
      },
    }
  },
})
const editor = useEditor({
  content: props.modelValue,
  editable: !props.disabled,
  extensions: [StarterKit.configure({ heading: { levels: [2, 3] }, link: false, code: false, codeBlock: false }), CatalogImage.configure({ allowBase64: false })],
  editorProps: { attributes: { class: 'catalog-document', 'aria-label': '玩法富文本编辑区' } },
  onUpdate: ({ editor: value }) => emit('update:modelValue', value.isEmpty ? '' : value.getHTML()),
})
watch(() => props.modelValue, value => {
  if (editor.value && (value || '') !== (editor.value.isEmpty ? '' : editor.value.getHTML())) editor.value.commands.setContent(value || '', { emitUpdate: false })
})
watch(() => props.disabled, value => editor.value?.setEditable(!value))
onBeforeUnmount(() => editor.value?.destroy())
const tools = [
  { label: '正文', text: '正文', run: () => editor.value?.chain().focus().setParagraph().run() },
  { label: '二级标题', text: '标题', active: () => !!editor.value?.isActive('heading', { level: 2 }), run: () => editor.value?.chain().focus().toggleHeading({ level: 2 }).run() },
  { label: '加粗', text: 'B', active: () => !!editor.value?.isActive('bold'), run: () => editor.value?.chain().focus().toggleBold().run() },
  { label: '斜体', text: 'I', active: () => !!editor.value?.isActive('italic'), run: () => editor.value?.chain().focus().toggleItalic().run() },
  { label: '下划线', text: 'U', active: () => !!editor.value?.isActive('underline'), run: () => editor.value?.chain().focus().toggleUnderline().run() },
  { label: '无序列表', text: '• 列表', active: () => !!editor.value?.isActive('bulletList'), run: () => editor.value?.chain().focus().toggleBulletList().run() },
  { label: '有序列表', text: '1. 列表', active: () => !!editor.value?.isActive('orderedList'), run: () => editor.value?.chain().focus().toggleOrderedList().run() },
  { label: '撤销', text: '↶', run: () => editor.value?.chain().focus().undo().run() },
  { label: '重做', text: '↷', run: () => editor.value?.chain().focus().redo().run() },
]
async function uploadImage(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]
  if (!file) return
  input.value = ''
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) { notice.value = '请上传 10MB 以内的 PNG、JPEG 或 WebP 图片'; return }
  uploading.value = true; emit('uploading', true)
  try {
    const { data } = await catalogImageApi.upload(file)
    const apiRoot = new URL(import.meta.env.VITE_API_BASE || '/api/admin', window.location.origin)
    const src = new URL(data.url, apiRoot).href
    const originalSrc = new URL(data.original_url || data.url, apiRoot).href
    editor.value?.chain().focus().insertContent({ type: 'image', attrs: { src, originalSrc, alt: file.name } }).run()
    notice.value = '图片已插入，请保存文案'
  } finally { uploading.value = false; emit('uploading', false) }
}
</script>

<style scoped>
.catalog-editor { width:100%; border:1px solid var(--border); border-radius:10px; overflow:hidden; }
.editor-toolbar { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); padding:var(--space-3); border-bottom:1px solid var(--border); background:var(--muted); }
.editor-toolbar button, .image-upload { display:inline-flex; align-items:center; justify-content:center; min-height:var(--control-height-sm); border:0; border-radius:var(--control-radius); background:transparent; color:var(--foreground); padding:6px 10px; font-size:13px; cursor:pointer; line-height:20px; }
.editor-toolbar button.active, .editor-toolbar button:hover, .image-upload:hover { color:var(--primary); background:var(--accent); }
.editor-toolbar button:disabled, .busy { opacity:.5; pointer-events:none; }
.image-upload input { display:none; }
.editor-hint { padding:10px 14px; line-height:1.6; font-size:12px; color:var(--muted-foreground); border-top:1px solid var(--border); }
:deep(.catalog-document) { min-height:220px; max-height:480px; overflow:auto; padding:var(--space-5); font-size:14px; line-height:1.8; overflow-wrap:anywhere; }
:deep(.catalog-document:focus-visible) { outline:2px solid var(--primary); outline-offset:-2px; }
:deep(.catalog-document p) { margin:0 0 12px; }
:deep(.catalog-document h2), :deep(.catalog-document h3) { margin:20px 0 10px; line-height:1.4; }
:deep(.catalog-document img) { display:block; max-width:100%; height:auto; margin:12px 0; }
:deep(.catalog-document img.ProseMirror-selectednode) { outline:3px solid var(--primary); }
:deep(.catalog-document ul), :deep(.catalog-document ol) { padding-left:24px; }
.preview-wrap { padding:var(--space-5); background:var(--xa-color-surface-hover); }
.mobile-preview { max-width:375px; margin:auto; background:var(--xa-color-surface-card); color:var(--xa-color-text-primary); border:1px solid var(--border); border-radius:var(--radius-sm); }
</style>
