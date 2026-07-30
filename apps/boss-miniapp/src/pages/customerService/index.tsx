import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Image, ScrollView, Input } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import {
  ChatMessage,
  ChatMessagePush,
  fetchChatSession,
  markChatRead,
  sendChatImage,
  sendChatText,
} from '@/services/chat';
import { wsService } from '@/services/websocket';
import { Empty } from '@/components';
import Icon from '@/components/Icon';
import { resolveImageUrl } from '@/utils/media';
import styles from './index.module.scss';

const formatTime = (dateStr: string): string => {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  const h = date.getHours().toString().padStart(2, '0');
  const m = date.getMinutes().toString().padStart(2, '0');
  return `${date.getMonth() + 1}/${date.getDate()} ${h}:${m}`;
};

const CustomerServicePage: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [scrollTop, setScrollTop] = useState(0);
  const sessionIdRef = useRef<number>(0);

  const scrollToBottom = () => {
    setScrollTop(prev => prev + 100000);
  };

  const loadSession = async () => {
    const res = await fetchChatSession();
    if (res.code === 0 && res.data) {
      sessionIdRef.current = res.data.session.id;
      setMessages(res.data.messages || []);
      setTimeout(scrollToBottom, 50);
      markChatRead();
    }
  };

  useEffect(() => {
    loadSession();
    const off = wsService.on('chat_message', (data) => {
      const push = data as ChatMessagePush;
      if (!push?.message) return;
      if (sessionIdRef.current && push.session_id !== sessionIdRef.current) return;
      setMessages(prev => {
        if (prev.some(m => m.id === push.message.id)) return prev;
        return [...prev, push.message];
      });
      setTimeout(scrollToBottom, 50);
      if (push.message.is_from_support) {
        markChatRead();
      }
    });
    return () => {
      off();
    };
  }, []);

  useDidShow(() => {
    markChatRead();
  });

  const handleSendText = async () => {
    const content = input.trim();
    if (!content || sending) return;
    setSending(true);
    try {
      const res = await sendChatText(content);
      if (res.code === 0 && res.data) {
        setInput('');
        setMessages(prev => (prev.some(m => m.id === res.data!.id) ? prev : [...prev, res.data!]));
        setTimeout(scrollToBottom, 50);
      } else {
        Taro.showToast({ title: res.msg || '发送失败', icon: 'none' });
      }
    } catch (e) {
      Taro.showToast({ title: '发送失败', icon: 'none' });
    } finally {
      setSending(false);
    }
  };

  const handleSendImage = async () => {
    if (sending) return;
    try {
      const choose = await Taro.chooseImage({ count: 1, sizeType: ['compressed'], sourceType: ['album', 'camera'] });
      const filePath = choose.tempFilePaths?.[0];
      if (!filePath) return;
      setSending(true);
      const res = await sendChatImage(filePath);
      if (res.code === 0 && res.data) {
        setMessages(prev => (prev.some(m => m.id === res.data!.id) ? prev : [...prev, res.data!]));
        setTimeout(scrollToBottom, 50);
      } else {
        Taro.showToast({ title: res.msg || '发送失败', icon: 'none' });
      }
    } catch (e) {
      /* 用户取消选择或上传失败 */
    } finally {
      setSending(false);
    }
  };

  const previewImage = (url: string) => {
    if (url) Taro.previewImage({ urls: [url], current: url });
  };

  return (
    <View className={styles.container}>
      <ScrollView className={styles.msgList} scrollY scrollTop={scrollTop} scrollWithAnimation>
        {messages.map((msg) => (
          <View
            key={msg.id}
            className={`${styles.msgRow} ${msg.is_from_support ? styles.rowLeft : styles.rowRight}`}
          >
            {msg.is_from_support && (
              <View className={styles.avatar}>
                <Icon name="headphones" size={32} color="#FFFFFF" />
              </View>
            )}
            <View className={styles.bubbleWrap}>
              <Text className={styles.msgTime}>{formatTime(msg.created_at)}</Text>
              {msg.content_type === 'IMAGE' ? (
                <Image
                  className={styles.msgImage}
                  src={resolveImageUrl(msg.image_url)}
                  mode="widthFix"
                  onClick={() => previewImage(resolveImageUrl(msg.image_url))}
                />
              ) : (
                <View className={`${styles.bubble} ${msg.is_from_support ? styles.bubbleLeft : styles.bubbleRight}`}>
                  <Text className={styles.bubbleText}>{msg.content}</Text>
                </View>
              )}
            </View>
          </View>
        ))}
        {messages.length === 0 && (
          <Empty icon="💬" title="有问题随时找客服～" desc="发送消息开始对话，我们会尽快回复" />
        )}
      </ScrollView>

      <View className={styles.inputBar}>
        <View className={styles.imageBtn} onClick={handleSendImage}>
          <Icon name="plus" size={44} color="#8E8E93" />
        </View>
        <Input
          className={styles.input}
          value={input}
          placeholder="请输入您的问题…"
          confirmType="send"
          onInput={(e) => setInput(e.detail.value)}
          onConfirm={handleSendText}
        />
        <View
          className={`${styles.sendBtn} ${input.trim() ? styles.sendActive : ''}`}
          onClick={handleSendText}
        >
          <Icon name="send" size={36} color="#FFFFFF" />
        </View>
      </View>
    </View>
  );
};

export default CustomerServicePage;
