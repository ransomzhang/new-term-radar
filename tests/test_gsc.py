"""Offline contract checks. All data below is synthetic, never SEO evidence."""
import json
from datetime import date
from pathlib import Path

import pytest

from radar import gsc
from scripts import gsc_auth


@pytest.mark.parametrize('site,ok', [
    ('https://meme.ransomz.dev/', True), ('sc-domain:ransomz.dev', True),
    ('sc-domain:meme.ransomz.dev', True), ('sc-domain:evilransomz.dev', False),
    ('https://meme.ransomz.dev.evil/', False), ('http://meme.ransomz.dev/', False),
    ('https://meme.ransomz.dev/tools/', False),
])
def test_property_scope(site, ok):
    assert gsc.covers(site, 'https://meme.ransomz.dev/') is ok


def test_exact_access_and_target_are_verified(monkeypatch):
    monkeypatch.setattr(gsc, 'sites', lambda a: [{'siteUrl': 'sc-domain:ransomz.dev'}])
    assert gsc.verify('x', 'sc-domain:ransomz.dev', 'https://meme.ransomz.dev/')
    with pytest.raises(RuntimeError, match='not accessible'):
        gsc.verify('x', 'https://meme.ransomz.dev/')
    with pytest.raises(RuntimeError, match='does not cover'):
        gsc.verify('x', 'sc-domain:ransomz.dev', 'https://other.dev/')


def test_pagination_counts_unfiltered_rows(monkeypatch):
    monkeypatch.setattr(gsc, 'ROW_LIMIT', 2)
    calls = []
    def request(url, access, body):
        calls.append(body)
        rows = {
            0: [{'keys': ['site:bot'], 'impressions': 8, 'clicks': 0},
                {'keys': ['a'], 'impressions': 4, 'clicks': 1}],
            2: [{'keys': ['b'], 'impressions': 6, 'clicks': 0}],
        }
        return {'rows': rows[body['startRow']]}
    monkeypatch.setattr(gsc, '_request', request)
    result = gsc._query('x', 'sc-domain:ransomz.dev', date(2026, 1, 1), date(2026, 1, 3),
                        ['query'], False, 'https://meme.ransomz.dev/')
    assert [r['query'] for r in result] == ['a', 'b']
    assert [c['startRow'] for c in calls] == [0, 2]
    assert calls[0]['dataState'] == 'final'
    assert calls[0]['dimensionFilterGroups'][0]['filters'][0]['expression'] == r'^https://meme\.ransomz\.dev/'


def test_all_pages_and_daily_impressions_are_preserved():
    rows = [dict(query='test', page='/a', impressions=3, clicks=1, date='2026-01-01'),
            dict(query='test', page='/b', impressions=5, clicks=0, date='2026-01-02'),
            dict(query='old', page='/c', impressions=100, clicks=9, date='2026-01-02')]
    result = gsc.fresh_queries(rows, [{'query': 'old'}])
    assert len(result) == 1
    assert result[0]['page'] == '/b'
    assert result[0]['impressions'] == 8
    assert len(result[0]['pages']) == 2
    assert result[0]['daily'] == {'2026-01-01': 3, '2026-01-02': 5}


def test_missing_credentials_do_not_look_like_empty_results(cfg, monkeypatch):
    monkeypatch.delenv('GSC_TOKEN_JSON', raising=False)
    cfg['gsc'] = {'site': 'sc-domain:ransomz.dev'}
    with pytest.raises(RuntimeError, match='credentials missing'):
        gsc.gsc_new(cfg)


def test_non_readonly_token_rejected_before_network(cfg, monkeypatch):
    monkeypatch.setenv('GSC_TOKEN_JSON', json.dumps({'scopes': ['https://www.googleapis.com/auth/webmasters']}))
    with pytest.raises(RuntimeError, match='scope'):
        gsc._token(cfg)


def test_selection_uses_actual_property_not_a_guessed_one():
    entries = [{'siteUrl': 'sc-domain:ransomz.dev'}]
    assert gsc_auth.select_property(entries, 'https://meme.ransomz.dev/') == 'sc-domain:ransomz.dev'
    with pytest.raises(RuntimeError, match='exactly match'):
        gsc_auth.select_property(entries, 'https://meme.ransomz.dev/', 'https://meme.ransomz.dev/')


def test_private_files_are_restricted(tmp_path):
    path = tmp_path / '.secrets' / 'token.json'
    gsc_auth.private_write(path, 'synthetic')
    assert path.read_text() == 'synthetic'
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.iterdir()) == [path]


def test_windows_and_all_qualified_candidates(cfg, monkeypatch):
    cfg['gsc'] = {'site': 'sc-domain:ransomz.dev', 'target_url': 'https://meme.ransomz.dev/'}
    monkeypatch.setattr(gsc, '_token', lambda c: 'synthetic')
    monkeypatch.setattr(gsc, 'verify', lambda *a: {'siteUrl': a[1]})
    calls = []
    def query(access, site, start, end, dims, fresh, target):
        calls.append((start, end, dims, fresh, target))
        if dims == ['query']:
            return []
        return [{'query': f'test-{i}', 'page': '/a', 'impressions': 5, 'clicks': 0} for i in range(35)]
    monkeypatch.setattr(gsc, '_query', query)
    result = gsc.gsc_new(cfg)
    assert (calls[0][1] - calls[0][0]).days == 2
    assert (calls[1][1] - calls[1][0]).days == 59
    assert (calls[0][0] - calls[1][1]).days == 1
    assert not calls[0][3] and not calls[1][3]
    assert result['qualified_new'] == 35 and len(result['queries']) == 35
    assert result['data_state'] == 'final'
