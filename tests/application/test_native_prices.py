from unittest import TestCase

from gccp_foundation.application.information_prices import native_price_source, plan_prices
from gccp_foundation.application.native_price import verify_price_batch


def resource(quantity='2', price='0', total='0', source=''):
    return ['001', '材', '试验管件', 'DN80', '个', quantity, price, total, source, 'false', 'true'] + [''] * 7


def offer(name, price, source, brand=''):
    return dict(material=name, specification='DN80', unit='个', ex_tax=price, incl_tax=price,
                source=source, quote_month='2026-10', brand=brand)


class NativePriceTests(TestCase):
    def test_native_source_retains_market_quote_date(self):
        self.assertEqual(native_price_source(dict(source='市场价', quote_month='2026-08',
                                                  quote_date='2026-08-07')), '市场价(2026年8月7日)')
        self.assertEqual(native_price_source(dict(source='北京信息价（京标价协）', quote_month='2026-06')),
                         '北京信息价(2026年06月京标价协)')

    def test_source_priority_and_highest_reviewed_brand_quote(self):
        row = resource()
        aliases = [dict(target=row[:5], quotes=[['同口径管件', 'DN80', '个']], reason='同材质',
                        preferred_brand='指定品牌')]
        market = offer('试验管件', '20', '市场')
        matching = [offer('同口径管件', p, '房建', brand) for p, brand in
                    [('5.123', '指定品牌'), ('6.123', '指定品牌'), ('7.123', '其他')]]
        result = plan_prices([row], matching, [], market=[market], tax_basis='ex_tax', aliases=aliases)
        self.assertEqual(result['items'][0]['price'], '6.123')
        self.assertEqual(result['items'][0]['quote']['source'], '房建')

    def test_source_plan_rejects_cross_unit_substitution(self):
        row = resource()
        aliases = [dict(target=row[:5], quote=['试验管件', 'DN80', 't'], reason='含量未知')]
        with self.assertRaises(ValueError):
            plan_prices([row], [], [], tax_basis='ex_tax', aliases=aliases)

    def test_resource_merge_requires_conserved_quantity_and_price(self):
        pending = resource()
        existing = resource('3', '1.6800000000000002', '5.04', '前期')
        combined = resource('5', '1.6800000000000002', '8.40')
        key = tuple(pending[:6])
        before = {tuple(row[:6]): row for row in (pending, existing)}
        observed = verify_price_batch(before, {tuple(combined[:6]): combined}, {key: ('1.68', '本期')})
        self.assertEqual(observed[key], combined)
        combined[5] = '6'
        with self.assertRaises(ValueError):
            verify_price_batch(before, {tuple(combined[:6]): combined}, {key: ('1.68', '本期')})
