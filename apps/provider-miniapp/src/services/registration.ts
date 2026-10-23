import Taro from '@tarojs/taro';
import { BASE_URL } from '@/utils/env';
import type { ApiResponse } from '@/utils/request';

export interface RegistrationForm {
  username: string; password: string; phone: string; real_name: string;
  id_number: string; display_name: string; deposit_mode: 'CASH' | 'EARNINGS';
}
export type PhotoField = 'id_front' | 'id_back' | 'deposit_proof';
export interface RegistrationPhoto { path: string; name: string; size: number; file?: File; }
export type RegistrationPhotos = Partial<Record<PhotoField, RegistrationPhoto>>;
export interface RegistrationResult {
  id: number; username: string; display_name: string;
  status: 'PENDING' | 'APPROVED' | 'REJECTED'; status_display: string;
  review_note: string; reviewed_at: string | null; submitted_at: string;
  revision: number; deposit_required: number; level_name: string; deposit_mode: 'CASH' | 'EARNINGS';
}
export type RegistrationErrors = Partial<Record<keyof RegistrationForm | PhotoField | 'confirm_password', string>>;

export function validateRegistration(form: RegistrationForm, photos: RegistrationPhotos, confirmation: string, statusOnly = false): RegistrationErrors {
  const errors: RegistrationErrors = {};
  if (!/^[a-zA-Z0-9_]{4,32}$/.test(form.username.trim())) errors.username = '账号需为4至32位字母、数字或下划线';
  if (form.password.length < 8 || form.password.length > 128) errors.password = '密码需为8至128位';
  if (statusOnly) return errors;
  if (confirmation !== form.password) errors.confirm_password = '两次输入的密码不一致';
  if (!/^1[3-9]\d{9}$/.test(form.phone.trim())) errors.phone = '请填写有效手机号';
  if (form.real_name.trim().length < 2) errors.real_name = '请填写真实姓名';
  if (!/^[1-9]\d{16}[\dXx]$/.test(form.id_number.trim())) errors.id_number = '请填写18位身份证号码';
  if (!form.display_name.trim()) errors.display_name = '请填写结单昵称';
  (['id_front', 'id_back', 'deposit_proof'] as PhotoField[]).forEach(field => {
    if (field === 'deposit_proof' && form.deposit_mode === 'EARNINGS') return;
    if (!photos[field]) errors[field] = '请上传这张照片';
    else if (photos[field]!.size > 5 * 1024 * 1024) errors[field] = '照片不能超过5MB';
  });
  return errors;
}

function unpack(response: ApiResponse<RegistrationResult>): RegistrationResult {
  if (response.code !== 0 || !response.data) {
    const error = new Error(response.msg || '提交失败，请重试');
    const fields = (response.data as unknown as { fields?: Record<string, string[]> })?.fields;
    throw Object.assign(error, {
      fields: fields ? Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, value.join('；')])) : {},
    });
  }
  return response.data;
}

export async function queryRegistration(username: string, password: string) {
  const response = await Taro.request<ApiResponse<RegistrationResult>>({
    url: BASE_URL + '/provider-registration/status/', method: 'POST',
    data: { username: username.trim(), password }, timeout: 15000,
    header: { 'Content-Type': 'application/json' },
  });
  return unpack(response.data);
}

function utf8(value: string) {
  const encoded = encodeURIComponent(value);
  const bytes: number[] = [];
  for (let n = 0; n < encoded.length; n++) {
    if (encoded[n] === '%') { bytes.push(parseInt(encoded.slice(n + 1, n + 3), 16)); n += 2; }
    else bytes.push(encoded.charCodeAt(n));
  }
  return new Uint8Array(bytes);
}

export async function submitRegistration(form: RegistrationForm, photos: RegistrationPhotos, resubmit = false) {
  const values = { ...form, username: form.username.trim(), phone: form.phone.trim(), real_name: form.real_name.trim(),
    display_name: form.display_name.trim(), id_number: form.id_number.trim().toUpperCase() };
  const endpoint = BASE_URL + '/provider-registration/' + (resubmit ? 'resubmit/' : '');
  if (process.env.TARO_ENV === 'h5') {
    const body = new FormData();
    Object.entries(values).forEach(([key, value]) => body.append(key, value));
    for (const field of ['id_front', 'id_back', 'deposit_proof'] as PhotoField[]) {
      if (field === 'deposit_proof' && form.deposit_mode === 'EARNINGS') continue;
      const photo = photos[field];
      if (!photo) continue;
      const file = photo.file || await fetch(photo.path).then(response => response.blob());
      if (!file) throw new Error('照片读取失败，请重新选择');
      body.append(field, file, photo.name);
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(endpoint, { method: 'POST', body, credentials: 'omit', signal: controller.signal });
      return unpack(await response.json());
    } finally { clearTimeout(timer); }
  }

  // Miniapp uploadFile accepts one file. Build a single bounded multipart request for all three documents.
  const boundary = 'XARegistration' + Date.now() + Math.random().toString(36).slice(2);
  const chunks: Uint8Array[] = [];
  Object.entries(values).forEach(([key, value]) => chunks.push(utf8('--' + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + value + '\r\n')));
  for (const field of ['id_front', 'id_back', 'deposit_proof'] as PhotoField[]) {
    if (field === 'deposit_proof' && form.deposit_mode === 'EARNINGS') continue;
    const photo = photos[field];
    if (!photo) continue;
    const data = await new Promise<ArrayBuffer>((resolve, reject) => {
      Taro.getFileSystemManager().readFile({
        filePath: photo.path,
        success: result => typeof result.data === 'string' ? reject(new Error('照片读取失败')) : resolve(result.data),
        fail: () => reject(new Error('照片读取失败，请重新选择')),
      });
    });
    if (data.byteLength > 5 * 1024 * 1024) throw new Error('照片不能超过5MB');
    chunks.push(utf8('--' + boundary + '\r\nContent-Disposition: form-data; name="' + field + '"; filename="' + field + '.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'));
    chunks.push(new Uint8Array(data), utf8('\r\n'));
  }
  chunks.push(utf8('--' + boundary + '--\r\n'));
  const payload = new Uint8Array(chunks.reduce((length, chunk) => length + chunk.length, 0));
  let offset = 0;
  chunks.forEach(chunk => { payload.set(chunk, offset); offset += chunk.length; });
  const response = await Taro.request<ApiResponse<RegistrationResult>>({
    url: endpoint, method: 'POST', data: payload.buffer, timeout: 30000,
    header: { 'Content-Type': 'multipart/form-data; boundary=' + boundary },
  });
  return unpack(response.data);
}

export interface DepositRules { cash_target:number; earnings_target:number; earnings_multiplier:number; deposit_percent:number; settlement_percent:number; hold_hours:number }
export async function fetchRegistrationRules(): Promise<DepositRules> {
  const result = await Taro.request<ApiResponse<DepositRules>>({ url:BASE_URL + '/provider-registration/rules/', method:'GET', timeout:15000 });
  if (result.data.code !== 0 || !result.data.data) throw new Error('押金规则加载失败，请重试');
  return result.data.data;
}
