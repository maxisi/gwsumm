# -*- coding: utf-8 -*-
# Copyright (C) Duncan Macleod (2013)
#
# This file is part of GWSumm.
#
# GWSumm is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GWSumm is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GWSumm.  If not, see <http://www.gnu.org/licenses/>.

"""This module handles HDF archiving of data.
"""

import tempfile
import shutil

from gwpy.timeseries import TimeSeries
from gwpy.spectrogram import Spectrogram
from gwpy.segments import DataQualityFlag

from . import (globalv, version)
from .data import (get_channel, add_timeseries, add_spectrogram)

__author__ = 'Duncan Macleod <duncan.macleod@ligo.org>'
__version__ = version.version


def write_data_archive(outfile, timeseries=True, spectrogram=True,
                       segments=True):
    """Build and save an HDF archive of data processed in this job.

    Parameters
    ----------
    outfile : `str`
        path to target HDF5 file
    timeseries : `bool`, default: `True`
        include `TimeSeries` data in archive
    spectrogram : `bool`, default: `True`
        include `Spectrogram` data in archive
    """
    from h5py import File

    backup = backup_existing_archive(outfile)

    try:
        with File(outfile, 'w') as h5file:
            # record all time-series data
            if timeseries:
                group = h5file.create_group('timeseries')
                # loop over channels
                for tslist in globalv.DATA.itervalues():
                    # loop over time-series
                    for ts in tslist:
                        name = '%s,%s,%s' % (ts.name, ts.channel.ndsname,
                                             ts.epoch.gps)
                        ts.write(group, name=name)

            # record all spectrogram data
            if spectrogram:
                group = h5file.create_group('spectrogram')
                # loop over channels
                for speclist in globalv.SPECTROGRAMS.itervalues():
                    # loop over time-series
                    for spec in speclist:
                        name = '%s,%s' % (spec.name, spec.epoch.gps)
                        spec.write(group, name=name)

            # record all segment data
            if segments:
                group = h5file.create_group('segments')
                # loop over channels
                for dqflag in globalv.SEGMENTS.itervalues():
                    # loop over time-series
                    dqflag.write(group)
    except:
        if backup:
            restore_backup(backup, outfile)
        raise


def read_data_archive(sourcefile):
    """Read archived data from an HDF5 archive source.

    Parameters
    ----------
    sourcefile : `str`
        path to source HDF5 file
    """
    from h5py import File

    with File(sourcefile, 'r') as h5file:
        # read all time-series data
        try:
            group = h5file['timeseries']
        except KeyError:
            group = dict()
        for dataset in group.itervalues():
            ts = TimeSeries.read(dataset)
            ts.channel = get_channel(ts.channel)
            add_timeseries(ts, key=ts.channel.ndsname)

        # read all spectrogram data
        try:
            group = h5file['spectrogram']
        except KeyError:
            group = dict()
        for dataset in group.itervalues():
            spec = Spectrogram.read(dataset)
            spec.channel = get_channel(spec.channel)
            add_spectrogram(spec)

        try:
            group = h5file['segments']
        except KeyError:
            group = dict()
        for dataset in group.itervalues():
            dqflag = DataQualityFlag.read(dataset)
            globalv.SEGMENTS += {dqflag.name: dqflag}


def backup_existing_archive(filename, suffix='.hdf',
                            prefix='gw_summary_archive_', dir=None):
    """Create a copy of an existing archive.
    """
    backup = tempfile.mktemp(suffix=suffix, prefix=prefix, dir=dir)
    try:
        shutil.move(filename, backup)
    except IOError:
        return None
    else:
        return backup


def restore_backup(backup, target):
    """Reinstate a backup copy of the archive.
    """
    shutil.move(backup, target)