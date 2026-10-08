import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { isIP } from 'node:net';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export function validateReleaseConfig({ env, project, privateProject = {}, nativeConfig, target = 'release' }) {
  const errors = [];
  function publicUrl(name, value, protocol, { api = false, root = false } = {}) {
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase();
      if (typeof value !== 'string' || value !== value.trim() || url.protocol !== protocol ||
          !host.includes('.') || isIP(host.replace(/^\[|\]$/g, '')) ||
          !/^[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(host) ||
          /(^|\.)(localhost|local|internal|test|invalid|example)$/.test(host) ||
          /(^|\.)example\.(com|org|net)$/.test(host) || url.username || url.password || url.search || url.hash ||
          (api && !/\/api\/?$/.test(url.pathname)) || (root && url.pathname !== '/')) throw new Error();
      return url;
    } catch {
      errors.push(`${name} 必须为公网 ${protocol} 地址${api ? '，并以 /api 结尾' : root ? '，只填写域名，不含业务路径' : ''}`);
    }
  }

  if (!['trial', 'release'].includes(target)) errors.push('目标环境只能为 trial 或 release');
  if (project.miniprogramRoot !== 'native/') errors.push('老板端 project.config.json 的 miniprogramRoot 必须为 native/');
  if (project.compileType !== 'miniprogram') errors.push('老板端 compileType 必须为 miniprogram');
  const effectiveSettings = { ...project.setting, ...privateProject.setting };
  if (effectiveSettings.urlCheck !== true) errors.push('微信开发者工具必须开启 urlCheck；project.private.config.json 的同名设置会覆盖公共配置');

  const native = nativeConfig?.[target];
  publicUrl(`native/config.js ${target}.apiBase`, native?.apiBase, 'https:', { api: true });
  if (native?.socketBase) publicUrl(`native/config.js ${target}.socketBase`, native.socketBase, 'wss:', { root: true });

  // 正式 H5 可由 HTTPS 网关同源反代；原生小程序不能使用相对地址。
  if (env.TARO_APP_API !== '/api') publicUrl('陪玩 H5 TARO_APP_API', env.TARO_APP_API, 'https:', { api: true });
  if (env.VITE_API_BASE && !/^\/api\/admin\/?$/.test(env.VITE_API_BASE)) {
    const adminUrl = publicUrl('管理后台 VITE_API_BASE', env.VITE_API_BASE, 'https:');
    if (adminUrl && !/^\/api\/admin\/?$/.test(adminUrl.pathname)) errors.push('管理后台 VITE_API_BASE 必须以 /api/admin 结尾');
  }
  if (!/^wx[a-f0-9]{16}$/.test(env.WECHAT_APPID || '')) errors.push('WECHAT_APPID 必须为小程序 AppID');
  else if (env.WECHAT_APPID !== project.appid) errors.push('WECHAT_APPID 与老板端 project.config.json 不一致');
  return errors;
}

export function checkReleaseConfig(argv = process.argv.slice(2), env = process.env) {
  const targetArg = argv.find(arg => arg.startsWith('--target='));
  const target = targetArg ? targetArg.slice('--target='.length) : 'release';
  if (argv.some(arg => arg !== targetArg) || argv.length > 1) throw new Error('用法：node scripts/check-release-config.mjs [--target=trial|release]');
  const projectUrl = new URL('../apps/boss-miniapp/project.config.json', import.meta.url);
  const privateUrl = new URL('../apps/boss-miniapp/project.private.config.json', import.meta.url);
  const project = JSON.parse(readFileSync(projectUrl, 'utf8'));
  const privateProject = existsSync(privateUrl) ? JSON.parse(readFileSync(privateUrl, 'utf8')) : {};
  const nativeConfig = createRequire(import.meta.url)('../apps/boss-miniapp/native/config.js');
  return { target, errors: validateReleaseConfig({ env, project, privateProject, nativeConfig, target }) };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const { target, errors } = checkReleaseConfig();
    if (errors.length) {
      process.stderr.write(`发布配置未通过（${target}）：\n${errors.map(error => `- ${error}`).join('\n')}\n`);
      process.exitCode = 1;
    } else {
      process.stdout.write(`${target} 配置检查通过。仍需官方编译器、合法域名配置及微信真机验收；本检查不验证域名可达性、微信账号权限或执行上传。\n`);
    }
  } catch (error) {
    process.stderr.write(`无法读取发布配置：${error.message}\n`);
    process.exitCode = 1;
  }
}
