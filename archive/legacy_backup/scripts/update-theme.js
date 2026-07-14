#!/usr/bin/env node

/**
 * 主题色更新脚本
 * 用法: node scripts/update-theme.js [primary-color] [primary-light] [primary-dark]
 * 示例: node scripts/update-theme.js #4A9EFF #6BB6FF #3A8EEF
 */

const fs = require('fs');
const path = require('path');

// 默认蓝色主题
const defaultColors = {
  primary: '#4A9EFF',
  primaryLight: '#6BB6FF',
  primaryDark: '#3A8EEF'
};

// 获取命令行参数
const args = process.argv.slice(2);
const colors = {
  primary: args[0] || defaultColors.primary,
  primaryLight: args[1] || defaultColors.primaryLight,
  primaryDark: args[2] || defaultColors.primaryDark
};

// 验证颜色格式
function validateColor(color) {
  return /^#[0-9A-Fa-f]{6}$/.test(color);
}

if (!validateColor(colors.primary) || !validateColor(colors.primaryLight) || !validateColor(colors.primaryDark)) {
  console.error('错误: 颜色格式不正确，请使用 #RRGGBB 格式');
  console.error('示例: node scripts/update-theme.js #4A9EFF #6BB6FF #3A8EEF');
  process.exit(1);
}

// 主题文件路径
const themeFilePath = path.join(__dirname, '../src/styles/theme.scss');

// 读取当前主题文件
let themeContent = fs.readFileSync(themeFilePath, 'utf-8');

// 更新主题色
const originalPrimary = themeContent.match(/\$color-primary:\s*(#[0-9A-Fa-f]+)/)?.[1];
const originalPrimaryLight = themeContent.match(/\$color-primary-light:\s*(#[0-9A-Fa-f]+)/)?.[1];
const originalPrimaryDark = themeContent.match(/\$color-primary-dark:\s*(#[0-9A-Fa-f]+)/)?.[1];

console.log('当前主题色:');
console.log(`  主色: ${originalPrimary}`);
console.log(`  浅色: ${originalPrimaryLight}`);
console.log(`  深色: ${originalPrimaryDark}`);
console.log('');
console.log('新主题色:');
console.log(`  主色: ${colors.primary}`);
console.log(`  浅色: ${colors.primaryLight}`);
console.log(`  深色: ${colors.primaryDark}`);
console.log('');

// 替换颜色
themeContent = themeContent.replace(
  /(\$color-primary:\s*)#[0-9A-Fa-f]+/,
  `$1${colors.primary}`
);
themeContent = themeContent.replace(
  /(\$color-primary-light:\s*)#[0-9A-Fa-f]+/,
  `$1${colors.primaryLight}`
);
themeContent = themeContent.replace(
  /(\$color-primary-dark:\s*)#[0-9A-Fa-f]+/,
  `$1${colors.primaryDark}`
);
themeContent = themeContent.replace(
  /(\$color-info:\s*)#[0-9A-Fa-f]+/,
  `$1${colors.primary}`
);

// 写回文件
fs.writeFileSync(themeFilePath, themeContent, 'utf-8');

console.log('✅ 主题色已更新到 src/styles/theme.scss');
console.log('');
console.log('下一步:');
console.log('  1. 运行 npm run build:weapp 编译项目');
console.log('  2. 在微信开发者工具中预览效果');
console.log('');

// 可选：自动编译
if (args.includes('--build')) {
  console.log('正在自动编译...');
  const { execSync } = require('child_process');
  try {
    execSync('npm run build:weapp', { stdio: 'inherit', cwd: path.join(__dirname, '..') });
    console.log('✅ 编译完成');
  } catch (error) {
    console.error('❌ 编译失败:', error.message);
    process.exit(1);
  }
}
