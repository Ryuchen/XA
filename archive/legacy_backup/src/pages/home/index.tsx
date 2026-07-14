import React, { useState } from 'react';
import { View, Text, Image, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { mockProducts } from '@/data/mockProducts';
import { formatMoney, formatSales } from '@/utils/format';
import styles from './index.module.scss';

const HomePage: React.FC = () => {
  const [searchValue, setSearchValue] = useState('');

  const handleProductClick = (productId: number) => {
    Taro.navigateTo({
      url: `/pages/productDetail/index?id=${productId}`
    });
  };

  const handleSearch = () => {
    if (!searchValue.trim()) {
      Taro.showToast({ title: '请输入搜索关键词', icon: 'none' });
      return;
    }
    Taro.showToast({ title: '搜索功能开发中', icon: 'none' });
  };

  return (
    <ScrollView className={styles.container} scrollY>
      <View className={styles.header}>
        <View className={styles.searchBar}>
          <Text className={styles.searchIcon}>🔍</Text>
          <Input
            className={styles.searchPlaceholder}
            placeholder="搜索陪玩游戏"
            value={searchValue}
            onInput={(e) => setSearchValue(e.detail.value)}
            onConfirm={handleSearch}
          />
        </View>
      </View>

      <View className={styles.banner}>
        <Image
          className={styles.bannerImage}
          src="https://picsum.photos/id/1015/750/320"
          mode="aspectFill"
        />
      </View>

      <View className={styles.section}>
        <Text className={styles.sectionTitle}>热门推荐</Text>
        <View className={styles.productGrid}>
          {mockProducts.slice(0, 4).map((product) => (
            <View
              key={product.id}
              className={styles.productCard}
              onClick={() => handleProductClick(product.id)}
            >
              <Image
                className={styles.productImage}
                src={product.images[0]}
                mode="aspectFill"
              />
              <View className={styles.productInfo}>
                <Text className={styles.productName}>{product.name}</Text>
                <Text className={styles.productDesc}>{product.description}</Text>
                <View className={styles.priceRow}>
                  <Text className={styles.price}>¥{formatMoney(product.price)}</Text>
                  <Text className={styles.originalPrice}>¥{formatMoney(product.originalPrice)}</Text>
                  {product.tags[0] && <Text className={styles.tag}>{product.tags[0]}</Text>}
                  <Text className={styles.sales}>已售{formatSales(product.sales)}</Text>
                </View>
              </View>
            </View>
          ))}
        </View>
      </View>

      <View className={styles.section}>
        <Text className={styles.sectionTitle}>新品上架</Text>
        <View className={styles.productGrid}>
          {mockProducts.slice(2, 6).map((product) => (
            <View
              key={product.id}
              className={styles.productCard}
              onClick={() => handleProductClick(product.id)}
            >
              <Image
                className={styles.productImage}
                src={product.images[0]}
                mode="aspectFill"
              />
              <View className={styles.productInfo}>
                <Text className={styles.productName}>{product.name}</Text>
                <Text className={styles.productDesc}>{product.description}</Text>
                <View className={styles.priceRow}>
                  <Text className={styles.price}>¥{formatMoney(product.price)}</Text>
                  <Text className={styles.originalPrice}>¥{formatMoney(product.originalPrice)}</Text>
                  <Text className={styles.sales}>已售{formatSales(product.sales)}</Text>
                </View>
              </View>
            </View>
          ))}
        </View>
      </View>
    </ScrollView>
  );
};

export default HomePage;
