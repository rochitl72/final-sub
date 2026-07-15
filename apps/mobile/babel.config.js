module.exports = function (api) {
  api.cache(true);
  return {
    presets: [
      [
        'babel-preset-expo',
        {
          // Disable auto-inclusion of react-native-reanimated plugin
          // (we use React Native's built-in Animated API instead)
          reanimated: false,
        },
      ],
    ],
  };
};
