"""Local terrain must serve new locations offline and survive data-pack transfer."""
from contextlib import closing
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform

from gps_rssi.elevation import ElevationCache
from gps_rssi.local_dem import LocalDEM
from gps_rssi.offline import OfflineData


def terrain(path, *, value=123, crs='EPSG:26911', tagged=True):
    values = np.full((4, 4), value, dtype='float32')
    values[0, 0] = -9999
    with rasterio.open(path, 'w', driver='GTiff', count=1, dtype='float32',
                       width=4, height=4, crs=crs, transform=from_origin(412000, 3759000, 1, 1), nodata=-9999) as src:
        src.write(values, 1)
        if tagged:
            src.update_tags(vertical_datum='NAVD88', units='meters', source='Fixture survey')
    xs, ys = transform('EPSG:26911', 'EPSG:4326', [412001.5, 412000.5, 412100], [3758998.5, 3758999.5, 3759000])
    return [(round(y, 6), round(x, 6)) for x, y in zip(xs, ys)]


class LocalDEMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'first/data'
        self.offline = OfflineData(self.data)
        self.cache = ElevationCache(self.data / '.gps-rssi-elevation/cache.sqlite')
        self.path = self.root / 'terrain.tif'
        self.valid, self.nodata, self.outside = terrain(self.path)

    def test_local_precedes_cached_elevation_even_on_refresh_without_network(self):
        lat, lon = self.valid
        with closing(self.cache.connect()) as db, db:
            db.execute('INSERT INTO elevation VALUES (?,?,999,1,NULL,0)', (lat, lon))
        self.offline.import_terrain(self.path)
        self.offline.set_enabled(True)
        with patch('gps_rssi.elevation.urlopen', side_effect=AssertionError('network forbidden')):
            response = self.cache.get_many([dict(lat=lat, lon=lon)], refresh=True)
        sample = response['samples'][0]
        self.assertEqual(sample['elevation'], 123)
        self.assertTrue(sample['local'])
        self.assertEqual(sample['metadata']['resolution_m'], 1)
        self.assertEqual(sample['metadata']['vertical_datum'], 'NAVD88')
        self.assertEqual(response['source'], 'Local terrain GeoTIFF')
        # Removing local terrain restores the original cache, not a stale local value.
        LocalDEM(self.data).remove()
        self.assertEqual(self.cache.get(self.valid)['elevation'], 999)

    def test_nodata_and_outside_remain_unknown_offline_and_queue_for_fallback(self):
        self.offline.import_terrain(self.path)
        self.offline.set_enabled(True)
        with patch('gps_rssi.elevation.urlopen', side_effect=AssertionError('network forbidden')):
            for point in (self.nodata, self.outside):
                self.assertIsNone(self.cache.get(point)['elevation'])
        self.assertEqual(self.offline.status()['pending']['elevation'], 2)
        self.offline.set_enabled(False)
        with patch('gps_rssi.elevation.urlopen', return_value=io.BytesIO(b'{"value":456}')):
            self.assertEqual(self.cache.get(self.outside)['elevation'], 456)

    def test_uncovered_first_point_with_failed_network_does_not_hide_local_samples(self):
        self.offline.import_terrain(self.path)
        with patch('gps_rssi.elevation.urlopen', side_effect=OSError('no connectivity')):
            result = self.cache.get_many([dict(lat=p[0], lon=p[1]) for p in (self.outside, self.valid)])
        self.assertIsNone(result['samples'][0]['elevation'])
        self.assertEqual(result['samples'][1]['elevation'], 123)
        self.assertTrue(result['samples'][1]['local'])

    def test_replacement_is_immediate_and_failed_import_preserves_previous(self):
        self.offline.import_terrain(self.path)
        replacement = self.root / 'replacement.tif'
        terrain(replacement, value=234)
        self.offline.import_terrain(replacement)
        self.assertEqual(self.cache.get(self.valid)['elevation'], 234)
        terrain(replacement, tagged=False)
        with self.assertRaisesRegex(ValueError, 'NAVD88'):
            self.offline.import_terrain(replacement)
        self.assertEqual(self.cache.get(self.valid)['elevation'], 234)
        terrain(replacement, crs='EPSG:32611')
        with self.assertRaisesRegex(ValueError, 'EPSG:26911'):
            self.offline.import_terrain(replacement)
        terrain(replacement, value=float('nan'))
        with self.assertRaisesRegex(ValueError, 'invalid elevations'):
            self.offline.import_terrain(replacement)

    def test_offline_pack_carries_raster_and_new_unqueried_locations(self):
        self.offline.import_terrain(self.path)
        pack = self.root / 'offline.zip'
        self.offline.export_pack(pack)
        other = OfflineData(self.root / 'second/data')
        other.import_pack(pack)
        other.set_enabled(True)
        with patch('gps_rssi.elevation.urlopen', side_effect=AssertionError('network forbidden')):
            cache = ElevationCache(other.data / '.gps-rssi-elevation/cache.sqlite')
            self.assertEqual(cache.get(self.valid)['elevation'], 123)
        self.assertEqual(other.status()['terrain']['sha256'], self.offline.status()['terrain']['sha256'])

    def test_invalid_packed_terrain_is_rejected_before_cache_or_raster_changes(self):
        self.offline.import_terrain(self.path)
        pack = self.root / 'original.zip'
        self.offline.export_pack(pack)
        bad = self.root / 'invalid.zip'
        with zipfile.ZipFile(pack) as source, zipfile.ZipFile(bad, 'w') as target:
            for name in source.namelist():
                target.writestr(name, b'not a GeoTIFF' if name == 'terrain.tif' else source.read(name))
        other = OfflineData(self.root / 'second/data')
        terrain(self.path, value=234)
        other.import_terrain(self.path)
        before = other.status()['terrain']['sha256']
        with self.assertRaises((OSError, ValueError)):
            other.import_pack(bad)
        self.assertEqual(other.status()['terrain']['sha256'], before)
        self.assertEqual(other.status()['elevations'], 0)
