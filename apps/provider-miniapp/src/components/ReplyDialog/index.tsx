import React, { useEffect, useState } from 'react';
import { Button, Text, Textarea, View } from '@tarojs/components';
import { createPortal } from 'react-dom';
import styles from './index.module.scss';

interface ReplyDialogProps {
  open: boolean;
  title: string;
  initialValue?: string;
  placeholder?: string;
  maxLength?: number;
  submitting?: boolean;
  onClose: () => void;
  onSubmit: (content: string) => void | Promise<void>;
}

const ReplyDialog: React.FC<ReplyDialogProps> = ({
  open,
  title,
  initialValue = '',
  placeholder = '请输入回复内容',
  maxLength = 1000,
  submitting = false,
  onClose,
  onSubmit,
}) => {
  const [draft, setDraft] = useState(initialValue);

  useEffect(() => {
    if (open) setDraft(initialValue);
  }, [open, initialValue]);

  useEffect(() => {
    if (!open || typeof document === 'undefined') return undefined;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, [open]);

  if (!open) return null;

  const submit = () => {
    const content = draft.trim();
    if (content && !submitting) onSubmit(content);
  };

  const dialog = (
    <View className={styles.mask}>
      <View className={styles.dialog}>
        <View className={styles.handle} />
        <View className={styles.header}>
          <Text className={styles.title}>{title}</Text>
          <Text className={styles.close} onClick={() => !submitting && onClose()}>取消</Text>
        </View>
        <Textarea
          className={styles.textarea}
          value={draft}
          maxlength={maxLength}
          focus
          cursorSpacing={24}
          adjustPosition
          showConfirmBar={false}
          placeholder={placeholder}
          onInput={event => setDraft(event.detail.value)}
        />
        <View className={styles.meta}>
          <Text className={styles.hint}>回复发布后老板可见</Text>
          <Text className={styles.counter}>{draft.length}/{maxLength}</Text>
        </View>
        <Button
          className={styles.submit}
          loading={submitting}
          disabled={submitting || !draft.trim()}
          onClick={submit}
        >
          发布回复
        </Button>
      </View>
    </View>
  );

  return typeof document !== 'undefined' ? createPortal(dialog, document.body) : dialog;
};

export default ReplyDialog;
