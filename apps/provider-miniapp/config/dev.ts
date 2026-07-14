import type { UserConfigExport } from '@tarojs/cli';
export default {
  logger: {
    quiet: false,
    stats: true,
  },
  mini: {},
  h5: {
    devServer: {
      open: false,
      client: {
        overlay: false,
      },
    },
  },
} satisfies UserConfigExport<'webpack5'>;
