"""店舗マスタと、店名→store_code の解決。"""
import pytest

from hansoku.stores import Store, StoreMaster, UnknownStoreError


def _store(code: str, name: str, **overrides) -> Store:
    """テスト用の店舗。列が増えても壊れないようキーワードで組み立てる。"""
    fields = dict(
        store_code=code,
        store_name=name,
        source_name=overrides.pop("source_name", name),
        infomart_code="",
        infomart_name="",
        brand="X",
        brand_name="X",
        file_prefix="",
        is_shared_facility=False,
        active=True,
    )
    fields.update(overrides)
    return Store(**fields)


def test_全店が読み込める(master):
    assert len(master) == 24
    assert len(master.active) == 24


def test_store_codeが一意(master):
    codes = [s.store_code for s in master]
    assert len(codes) == len(set(codes))


def test_取り込み元の生の店名から引ける(master):
    assert master.by_name("0001015_すさび湯 歌舞伎町").store_code == "1015"


def test_表記揺れした店名からも引ける(master):
    assert master.by_name("すさび湯 歌舞伎町").store_code == "1015"


def test_コードの表記揺れを吸収する(master):
    assert master.by_code("01015").store_code == master.by_code(1015).store_code == "1015"


def test_店名が変わってもコードで引ける(master):
    """FWの店名表記が変わっても、埋め込まれたコードで正しい店に着地すること。"""
    assert master.find_by_name("0001154_熊の鳥焼 リニューアル").store_code == "1154"


def test_インフォマートのコードから引ける(master):
    """FWとインフォマートはコード体系が違う。熊の鳥焼は FW=1154 / インフォマート=922。"""
    store = master.by_infomart_code("922")
    assert store is not None and store.store_code == "1154"


def test_FWとインフォマートのコードが食い違う店がある(master):
    """取り違えると別の店の数字が混ざるため、食い違いを明示的に固定しておく。"""
    mismatched = [
        s for s in master
        if s.infomart_code and s.infomart_code != s.store_code
    ]
    # 24店中、インフォマート側の登録がある23店のうち10店で番号が食い違う。
    # （残る1店「ぎふや 福岡天神店」はインフォマート未登録なので比較対象外）
    assert len(mismatched) == 10


def test_未知の店名はエラーにする(master):
    with pytest.raises(UnknownStoreError):
        master.by_name("存在しない店")


def test_未知の店名はfind_by_nameならNone(master):
    assert master.find_by_name("存在しない店") is None


def test_全店にブランドが割り当てられている(master):
    assert all(s.brand for s in master)


def test_すさび湯は7店ある(master):
    assert sum(1 for s in master if s.brand == "SUSABIYU") == 7


def test_store_codeの重複は読み込み時に弾く():
    duplicated = [_store("1015", "A"), _store("1015", "B")]
    with pytest.raises(ValueError, match="重複"):
        StoreMaster(duplicated)


def test_正規化後に衝突する店名は読み込み時に弾く():
    """記号や長音を落とすため、店名の付け方次第では別の店が同じキーに潰れうる。"""
    colliding = [
        _store("1", "すさび湯 歌舞伎町", source_name="すさび湯　歌舞伎町"),
        _store("2", "すさび湯歌舞伎町"),
    ]
    with pytest.raises(ValueError, match="衝突"):
        StoreMaster(colliding)
