import { PropsWithChildren, ReactNode, useEffect, useId, useRef } from 'react';
import { Button, Text, View } from '@tarojs/components';
import { createPortal } from 'react-dom';
import styles from './index.module.scss';

type Props = PropsWithChildren<{ open: boolean; title: string; busy?: boolean; footer?: ReactNode; onClose: () => void }>;

/** Desktop dialog / mobile sheet, with one scroll body and visible actions. */
export default function SurfaceDialog({ open, title, busy = false, footer, onClose, children }: Props) {
  const id = `provider-dialog-${useId().replace(/:/g, '')}`;
  const closeRef = useRef(onClose);
  const busyRef = useRef(busy);
  closeRef.current = onClose;
  busyRef.current = busy;
  useEffect(() => {
    if (!open || process.env.TARO_ENV !== 'h5' || typeof document === 'undefined') return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const panel = document.getElementById(id);
    panel?.setAttribute('role', 'dialog');
    panel?.setAttribute('aria-modal', 'true');
    panel?.setAttribute('aria-labelledby', `${id}-title`);
    const targets = () => Array.from(panel?.querySelectorAll<HTMLElement>('input:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex="0"]') || []).filter(node => node.getClientRects().length && node.getAttribute('aria-disabled') !== 'true');
    const focusFrame = requestAnimationFrame(() => {
      const initial = panel?.querySelector<HTMLElement>('input:not([disabled]), textarea:not([disabled])') || targets()[0];
      initial?.focus();
    });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busyRef.current) { event.preventDefault(); closeRef.current(); }
      if (event.key !== 'Tab') return;
      const nodes = targets();
      if (!nodes.length) { event.preventDefault(); return; }
      const current = nodes.findIndex(node => node === document.activeElement);
      const next = current < 0 ? (event.shiftKey ? nodes.length - 1 : 0) : (current + (event.shiftKey ? -1 : 1) + nodes.length) % nodes.length;
      event.preventDefault();
      HTMLElement.prototype.focus.call(nodes[next]);
    };
    // Taro inputs stop the native event before emitting their custom event.
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.body.style.overflow = previousOverflow;
      cancelAnimationFrame(focusFrame);
      document.removeEventListener('keydown', onKey, true);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [open, id]);
  if (!open) return null;
  const dialog = <View className={styles.mask} onClick={() => !busy && onClose()}>
    <View id={id} className={styles.panel} onClick={event => event.stopPropagation()}>
      <View className={styles.handle} />
      <View className={styles.header}><Text id={`${id}-title`} className={styles.title}>{title}</Text><Button className={styles.close} size='mini' disabled={busy} onClick={onClose}>取消</Button></View>
      <View className={styles.body}>{children}</View>
      {footer && <View className={styles.footer}>{footer}</View>}
    </View>
  </View>;
  return process.env.TARO_ENV === 'h5' && typeof document !== 'undefined' ? createPortal(dialog, document.body) : dialog;
}
