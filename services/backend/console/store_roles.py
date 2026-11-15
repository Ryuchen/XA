"""Reserved platform-finance identities are not store roles or positions."""


def is_store_finance(code, name):
    normalized = str(code or '').strip().casefold().replace('-', '_')
    label = str(name or '').strip().casefold()
    return normalized in {'finance', 'store_finance', 'storefinance'} or label in {
        '财务', '门店财务', '店内财务', '店铺财务', 'finance', 'store finance',
    }
