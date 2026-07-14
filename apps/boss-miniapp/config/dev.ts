import type { UserConfigExport } from '@tarojs/cli';
export default {
  logger: {
    quiet: false,
    stats: true,
  },
  mini: {},
  h5: {
    devServer: {
      open: false, //禁止自动打开浏览器
      client: {
        overlay: false, // 走查时关闭运行时错误遮罩（H5 下 WS 不兼容会触发，无关业务）
      },
    },
  },
} satisfies UserConfigExport<'webpack5'>;
