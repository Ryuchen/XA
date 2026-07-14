import React, { useState } from 'react';
import { View, Text, Image, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { mockProducts, categories } from '@/data/mockProducts';
import { formatMoney } from '@/utils/format';
import styles from './index.module.scss';

const CategoryPage: React.FC = () => {
  const [activeCategory, setActiveCategory] = useState('全部');

  const filteredProducts = activeCategory === '全部'
    ? mockProducts
    : mockProducts.filter(p => p.category === activeCategory);

  const handleProductClick = (productId: number) => {
    Taro.navigateTo({
      url: `/pages/productDetail/index?id=${productId}`
    });
  };

  return (
    <View className={styles.container}>
      <ScrollView className={styles.categoryList} scrollY>
        {categories.map((category) => (
          <View
            key={category}
            className={`${styles.categoryItem} ${activeCategory === category ? styles.active : ''}`}
            onClick={() => setActiveCategory(category)}
          >
            <Text>{category}</Text>
          </View>
        ))}
      </ScrollView>

      <ScrollView className={styles.productList} scrollY>
        <Text className={styles.sectionTitle}>{activeCategory}</Text>
        <View className={styles.productGrid}>
          {filteredProducts.map((product) => (
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
                <View className={styles.priceRow}>
                  <Text className={styles.price}>¥{formatMoney(product.price)}</Text>
                  <Text className={styles.originalPrice}>¥{formatMoney(product.originalPrice)}</Text>
                </View>
              </View>
            </View>
          ))}
        </View>
      </ScrollView>
    </View>
  );
};

export default CategoryPage;
