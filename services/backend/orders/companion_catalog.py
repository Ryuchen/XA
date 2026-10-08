"""Latest confirmed companion naming and single-hour surcharge rules."""
def corrected_text(text):
    return (text.replace('娱乐女陪须安排女陪。', '下单可选择男陪或女陪。')
        .replace('娱乐女陪', '娱乐').replace('娱乐类单陪+10r/h', '娱乐／技术类单陪+10r/h'))


def corrected_service_fields(service):
    if service.service_type != 'COMPANION' or service.catalog_family != '常规陪玩':
        return {}
    changes = {}
    for field in ('name', 'catalog_variant', 'description', 'rules', 'details_html', 'purchase_note'):
        old = getattr(service, field)
        new = corrected_text(old)
        if service.billing_provider_count == 1 and service.catalog_variant.startswith('技术'):
            new = new.replace('100元／小时', '90元／小时').replace('100元/小时', '90元/小时')
        if field == 'rules' and '下单可选择男陪或女陪' not in new:
            new += '\n下单可选择男陪或女陪，价格相同。'
        if field == 'rules' and service.billing_provider_count == 1 and service.catalog_variant.startswith('技术'):
            if '技术档归入娱乐类' not in new:
                new += '\n技术档归入娱乐类，单陪加价10元／小时：80＋10＝90元／小时。'
        if new != old: changes[field] = new
    if service.billing_provider_count == 1 and service.catalog_variant.startswith('技术') and service.price == 10000:
        changes['price'] = 9000
    return changes
