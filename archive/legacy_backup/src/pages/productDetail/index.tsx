import React, { useState, useEffect } from 'react';
import { View, Text, Image, Swiper, SwiperItem, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { getProductById } from '@/data/mockProducts';
import { formatMoney, formatSales } from '@/utils/format';
import { Product } from '@/types/product';
import styles from './index.module.scss';

const ProductDetailPage: React.FC = () => {
  const [product, setProduct] = useState<Product | null>(null);
  const [selectedSpecs, setSelectedSpecs] = useState<Record<string, string>>({});
  const [currentImageIndex, setCurrentImageIndex] = useState(0);

  useEffect(() => {
    const { id } = Taro.getCurrentInstance().router?.params || {};
    if (id) {
      const productData = getProductById(Number(id));
      if (productData) {
        setProduct(productData);
        const initialSpecs: Record<string, string> = {};
        productData.specs.forEach(spec => {
          initialSpecs[spec.name] = spec.options[0];
        });
        setSelectedSpecs(initialSpecs);
      }
    }
  }, []);

  const handleSpecChange = (specName: string, option: string) => {
    setSelectedSpecs(prev => ({
      ...prev,
      [specName]: option
    }));
  };

  const handleAddCart = () => {
    Taro.showToast({
      title: '已加入购物车',
      icon: 'success'
    });
  };

  const handleBuyNow = () => {
    Taro.navigateTo({
      url: '/pages/serviceCard/index'
    });
  };

  if (!product) {
    return (
      <View className={styles.container}>
        <Text>加载中...</Text>
      </View>
    );
  }

  const discountPercent = Math.round((1 - product.price / product.originalPrice) * 100);

  return (
    <ScrollView className={styles.container} scrollY>
      <Swiper
        className={styles.swiper}
        indicatorColor="rgba(255,255,255,0.5)"
        indicatorActiveColor="#fff"
        current={currentImageIndex}
        onChange={(e) => setCurrentImageIndex(e.detail.current)}
        circular
      >
        {product.images.map((image, index) => (
          <SwiperItem key={index}>
            <Image className={styles.swiperImage} src={image} mode="aspectFill" />
          </SwiperItem>
        ))}
      </Swiper>

      <View className={styles.info}>
        <View className={styles.priceRow}>
          <Text className={styles.price}>¥{formatMoney(product.price)}</Text>
          <Text className={styles.originalPrice}>¥{formatMoney(product.originalPrice)}</Text>
          <Text className={styles.discount}>{discountPercent}折</Text>
        </View>
        <Text className={styles.productName}>{product.name}</Text>
        <Text className={styles.productDesc}>{product.description}</Text>
        <Text className={styles.unit}>单价: {product.price}元/小时起</Text>
        <View className={styles.tags}>
          {product.tags.map((tag, index) => (
            <Text key={index} className={styles.tag}>{tag}</Text>
          ))}
        </View>
        <View className={styles.salesInfo}>
          <Text className={styles.salesItem}>销量 {formatSales(product.sales)}</Text>
          <Text className={styles.salesItem}>库存 {product.stock}</Text>
        </View>
      </View>

      <View className={styles.section}>
        <Text className={styles.sectionTitle}>规格选择</Text>
        {product.specs.map((spec) => (
          <View key={spec.name} className={styles.specItem}>
            <Text className={styles.specLabel}>{spec.name}</Text>
            <View className={styles.specOptions}>
              {spec.options.map((option) => (
                <View
                  key={option}
                  className={`${styles.specOption} ${selectedSpecs[spec.name] === option ? styles.active : ''}`}
                  onClick={() => handleSpecChange(spec.name, option)}
                >
                  <Text>{option}</Text>
                </View>
              ))}
            </View>
          </View>
        ))}
      </View>

      <View className={styles.section}>
        <Text className={styles.sectionTitle}>商品详情</Text>
        <Text className={styles.productDesc}>{product.description}</Text>
        <View style="margin-top: 20rpx;">
          {product.images.map((image, index) => (
            <Image
              key={index}
              src={image}
              mode="widthFix"
              style="width: 100%; margin-bottom: 20rpx;"
            />
          ))}
        </View>
      </View>

      <View className={styles.footer}>
        <View className={styles.actionBtn} onClick={handleAddCart}>
          <Text>加入购物车</Text>
        </View>
        <View className={styles.actionBtn} onClick={handleBuyNow}>
          <Text>立即购买</Text>
        </View>
      </View>
    </ScrollView>
  );
};

export default ProductDetailPage;
