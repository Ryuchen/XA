import { useEffect, useRef, useState } from "react"
import { EditorContent, useEditor, useEditorState } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import Image from "@tiptap/extension-image"
import CatalogRichContent from "@/components/CatalogRichContent"
import { getSession } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Feedback } from "@/components/business"
import { request } from "@/api/request"

const CatalogImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      originalSrc: {
        default: null,
        parseHTML: (element) => element.getAttribute("data-original"),
        renderHTML: (attributes) =>
          attributes.originalSrc
            ? { "data-original": attributes.originalSrc }
            : {},
      },
    }
  },
})
export default function RichTextEditor({
  value,
  onChange,
  label = "图文内容",
  disabled = false,
  uploadPath,
  onUploading,
}: {
  value: string
  onChange: (value: string) => void
  label?: string
  disabled?: boolean
  uploadPath?: string
  onUploading?: (value: boolean) => void
}) {
  const [preview, setPreview] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState("")
  const alive = useRef(true)
  const currentOnChange = useRef(onChange)
  currentOnChange.current = onChange
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3] },
        link: false,
        code: false,
        codeBlock: false,
      }),
      CatalogImage.configure({ allowBase64: false }),
    ],
    content: value || "",
    editable: !disabled,
    editorProps: {
      attributes: {
        class:
          "rich-document min-h-56 max-h-[480px] overflow-y-auto p-4 text-sm leading-7 outline-none",
        role: "textbox",
        "aria-multiline": "true",
        "aria-label": label,
      },
    },
    onUpdate: ({ editor: instance }) =>
      currentOnChange.current(instance.isEmpty ? "" : instance.getHTML()),
  })
  useEditorState({
    editor,
    selector: ({ editor: instance }) =>
      instance
        ? {
            bold: instance.isActive("bold"),
            italic: instance.isActive("italic"),
            underline: instance.isActive("underline"),
            heading: instance.isActive("heading"),
            bullet: instance.isActive("bulletList"),
            ordered: instance.isActive("orderedList"),
          }
        : null,
  })
  useEffect(() => {
    if (editor && (value || "") !== (editor.isEmpty ? "" : editor.getHTML()))
      editor.commands.setContent(value || "", { emitUpdate: false })
  }, [editor, value])
  useEffect(() => {
    editor?.setEditable(!disabled && !uploading)
  }, [editor, disabled, uploading])
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      onUploading?.(false)
    }
  }, [])
  const tools = [
    {
      label: "正文",
      active: editor?.isActive("paragraph"),
      run: () => editor?.chain().focus().setParagraph().run(),
    },
    {
      label: "标题",
      active: editor?.isActive("heading"),
      run: () => editor?.chain().focus().toggleHeading({ level: 2 }).run(),
    },
    {
      label: "加粗",
      active: editor?.isActive("bold"),
      run: () => editor?.chain().focus().toggleBold().run(),
    },
    {
      label: "斜体",
      active: editor?.isActive("italic"),
      run: () => editor?.chain().focus().toggleItalic().run(),
    },
    {
      label: "下划线",
      active: editor?.isActive("underline"),
      run: () => editor?.chain().focus().toggleUnderline().run(),
    },
    {
      label: "无序列表",
      active: editor?.isActive("bulletList"),
      run: () => editor?.chain().focus().toggleBulletList().run(),
    },
    {
      label: "有序列表",
      active: editor?.isActive("orderedList"),
      run: () => editor?.chain().focus().toggleOrderedList().run(),
    },
    {
      label: "撤销",
      active: false,
      run: () => editor?.chain().focus().undo().run(),
    },
    {
      label: "重做",
      active: false,
      run: () => editor?.chain().focus().redo().run(),
    },
  ]
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <div
        className="flex flex-wrap gap-1 border-b bg-muted/40 p-2"
        role="toolbar"
        aria-label={`${label}格式`}
      >
        {tools.map((tool) => (
          <Button
            key={tool.label}
            type="button"
            size="sm"
            variant={tool.active ? "secondary" : "ghost"}
            aria-pressed={!!tool.active}
            disabled={disabled || uploading || !editor || preview}
            onClick={tool.run}
          >
            {tool.label}
          </Button>
        ))}
        {uploadPath && (
          <label
            className={`relative inline-flex min-h-9 cursor-pointer items-center rounded-md px-3 text-sm hover:bg-accent ${disabled || uploading ? "pointer-events-none opacity-50" : ""}`}
          >
            <span>{uploading ? "图片上传中…" : "上传图片"}</span>
            <input
              className="absolute inset-0 w-full cursor-pointer opacity-0"
              aria-label={`上传${label}图片`}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              disabled={disabled || uploading || !editor}
              onChange={async (event) => {
                const file = event.target.files?.[0]
                event.target.value = ""
                if (!file || !editor) return
                if (
                  !["image/png", "image/jpeg", "image/webp"].includes(
                    file.type
                  ) ||
                  file.size > 10 * 1024 * 1024
                ) {
                  setError("请上传 10MB 以内的 PNG、JPEG 或 WebP 图片。")
                  return
                }
                const revision = getSession("admin").revision
                setUploading(true)
                onUploading?.(true)
                setError("")
                try {
                  const data = new FormData()
                  data.append("image", file)
                  const result = (
                    await request({ url: uploadPath, method: "POST", data })
                  ).data
                  if (
                    !alive.current ||
                    revision !== getSession("admin").revision
                  )
                    return
                  const src = String(result.url)
                  const originalSrc = String(result.original_url || result.url)
                  editor
                    .chain()
                    .focus()
                    .insertContent({
                      type: "image",
                      attrs: { src, originalSrc, alt: file.name },
                    })
                    .run()
                  setPreview(false)
                } catch (cause: any) {
                  if (alive.current)
                    setError(cause.message || "图片上传失败，请重试。")
                } finally {
                  if (alive.current) {
                    setUploading(false)
                    onUploading?.(false)
                  }
                }
              }}
            />
          </label>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="ml-auto"
          disabled={uploading}
          onClick={() => setPreview(!preview)}
        >
          {preview ? "继续编辑" : "手机预览"}
        </Button>
      </div>
      {preview ? (
        <div className="max-h-[560px] overflow-y-auto bg-muted/30 p-4">
          <CatalogRichContent
            className="rich-document mx-auto min-h-56 max-w-[390px] rounded-lg border bg-background p-4 text-sm leading-7"
            html={editor?.getHTML() || value || ""}
          />
        </div>
      ) : (
        <EditorContent editor={editor} />
      )}
      <p className="border-t px-4 py-2 text-xs text-muted-foreground">
        支持标题、段落、文字格式和列表。
        {uploadPath ? "图片单张不超过 10MB，选中后可按删除键移除。" : ""}
      </p>
      <Feedback message={error} />
    </div>
  )
}
