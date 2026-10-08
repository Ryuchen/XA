import React, { useEffect, useState } from 'react';
import { Button, Text, Textarea, View } from '@tarojs/components';
import SurfaceDialog from '@/components/SurfaceDialog';
import styles from './index.module.scss';

interface ReplyDialogProps {
  open: boolean;
  title: string;
  initialValue?: string;
  placeholder?: string;
  maxLength?: number;
  submitting?: boolean;
  hint?: string;
  submitText?: string;
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
  hint = '回复发布后老板可见',
  submitText = '发布回复',
  onClose,
  onSubmit,
}) => {
  const [draft, setDraft] = useState(initialValue);

  useEffect(() => {
    if (open) setDraft(initialValue);
  }, [open, initialValue]);

  if (!open) return null;

  const submit = () => {
    const content = draft.trim();
    if (content && !submitting) onSubmit(content);
  };

  return (
    <SurfaceDialog open={open} title={title} busy={submitting} onClose={onClose} footer={
      <Button className={`xa-button-primary ${styles.submit}`} loading={submitting} disabled={submitting || !draft.trim()} onClick={submit}>{submitText}</Button>
    }>
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
          <Text className={styles.hint}>{hint}</Text>
          <Text className={styles.counter}>{draft.length}/{maxLength}</Text>
        </View>
    </SurfaceDialog>
  );
};

export default ReplyDialog;
