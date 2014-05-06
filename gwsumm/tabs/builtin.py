# -*- coding: utf-8 -*-
# Copyright (C) Duncan Macleod (2013)
#
# This file is part of GWSumm
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
# along with GWSumm.  If not, see <http://www.gnu.org/licenses/>

"""Definition of a summary SummaryTab.
"""

import re
import warnings
from StringIO import StringIO
from multiprocessing import Process
from time import sleep

from numpy import isclose

from glue.segmentsUtils import tosegwizard

from .registry import (get_tab, register_tab)
from ..plot import (PlotList, registry as plotregistry)
from .. import globalv
from ..data import (get_channel, get_timeseries_dict, get_spectrogram,
                    get_spectrum)
from ..segments import get_segments
from ..triggers import get_triggers
from ..utils import *
from ..config import *
from .. import html

from gwsumm import version
__author__ = 'Duncan Macleod <duncan.macleod@ligo.org>'
__version__ = version.version

Tab = get_tab('basic')
StateTab = get_tab('state')


class SimpleStateTab(StateTab):
    """A simple `StateTab` with plots and data source summaries.

    This is the 'default' `Tab` that all configuration tab- sections
    will be formatted for unless otherwise specified.
    """
    type = 'default'

    def __init__(self, name, layout=None, **kwargs):
        """Initialise a new :class:`SimpleStateTab`
        """
        super(SimpleStateTab, self).__init__(name, **kwargs)
        self.plots = PlotList()
        self.layout = layout

    @property
    def layout(self):
        """List of how many plots to display on each row in the output.

        By default this is ``1`` if the tab contains only 1 or 3 plots,
        or ``2`` if otherwise.
        The final number given in the list will be repeated as necessary.

        :type: `list` of `ints <int>`
        """
        return self._layout

    @layout.setter
    def layout(self, l):
        if isinstance(l, (str, unicode)):
            l = eval(l)
        self._layout = l

    # -------------------------------------------
    # SummaryTab configuration parser

    @classmethod
    def from_ini(cls, cp, section, plotdir='plots', **kwargs):
        """Define a new `SummaryTab` from the given section of the
        `ConfigParser`.

        Parameters
        ----------
        cp : :class:`~gwsumm.config.GWConfigParser`
            customised configuration parser containing given section
        section : `str`
            name of section to parse
        plotdir : `str`, optional, default: ``'plots'``
            output path for plots, relative to current directory

        Returns
        -------
        tab : :class:`SummaryTab`
            a new tab defined from the configuration
        """
        job = super(SimpleStateTab, cls).from_ini(cp, section, **kwargs)
        start, end = job.span

        # get layout
        if cp.has_option(section, 'layout'):
            try:
                layout = eval(cp.get(section, 'layout'))
            except NameError:
                raise ValueError("Cannot parse 'layout' for '%s' tab. Layout "
                                 "should be given as a comma-separated list "
                                 "of integers")
            if isinstance(layout, int):
                layout = [layout]
            for l in layout:
                if isinstance(l, (tuple, list)):
                    l = l[0]
                if not l in [1, 2, 3, 4, 6, 12]:
                    raise ValueError("Cannot print more than %d plots in a "
                                     "single row. The chosen layout value for "
                                     "each row must be a divisor of 12 to fit "
                                     "the Bootstrap scaffolding. For details "
                                     "see http://getbootstrap.com/2.3.2/"
                                     "scaffolding.html")
        else:
            layout = None
        job.layout = layout
        job._config = cp._sections[section]

        # -------------------
        # parse plot requests
        #    All config entries whose key is a single integer is
        #    interpreted as a requested plot.

        # find and order the plots
        requests = sorted([(int(opt), val) for (opt, val) in
                           cp.nditems(section) if opt.isdigit()],
                          key=lambda a: a[0])

        # parse plot definition
        for index, definition in requests:
            # find plot customisations within this section
            mods = {}
            for key, val in cp.nditems(section):
                if key.startswith('%d-' % index):
                    opt = re_cchar.sub('_', key.split('-', 1)[1].lower())
                    try:
                        mods[opt] = eval(val)
                    except (NameError, SyntaxError):
                        mods[opt] = val

            # parse definition for section references
            try:
                pdef, sources = [s[::-1] for s in
                                 re.split('[\s,]', definition[::-1], 1)]
            except ValueError:
                pdef = definition
                sources = []
            else:
                if not re_channel.match(sources) and cp.has_section(sources):
                    try:
                        sources = cp.get(sources, 'channels')
                    except NoOptionError:
                        pass
                sources = split_channels(sources)

            # if pdef refers to another config section, it must have a type
            if cp.has_section(pdef):
                type_ = cp.get(pdef, 'type')
                PlotClass = plotregistry.get_plot(type_)
            elif re.search('-histogram\Z', pdef):
                type_ = None
                etg, column = pdef.rsplit('-', 2)[:2]
                mods.setdefault('etg', etg)
                mods.setdefault('column', column)
                PlotClass = plotregistry.get_plot('trigger-histogram')
            elif re.search('-rate', pdef):
                type_ = None
                etg = pdef.rsplit('-', 1)[0]
                mods.setdefault('etg', etg)
                PlotClass = plotregistry.get_plot('trigger-rate')
            else:
                type_ = None
                PlotClass = plotregistry.get_plot(pdef)
            # if the plot definition declares multiple states
            if 'all_states' in mods:
                if type_:
                    plot = PlotClass.from_ini(cp, pdef, start, end, sources,
                                              state=None, outdir=plotdir,
                                              **mods)
                else:
                    plot = PlotClass(sources, start, end, state=None,
                                     outdir=plotdir, **mods)
                job.plots.append(plot)
            # otherwise define individually for multiple states
            else:
                for state in job.states:
                    if type_:
                        plot = PlotClass.from_ini(cp, pdef, start, end,
                                                  sources, state=state,
                                                  outdir=plotdir, **mods)
                    else:
                        plot = PlotClass(sources, start, end, state=state,
                                         outdir=plotdir, **mods)
                    job.plots.append(plot)

        return job

    # -------------------------------------------
    # SummaryTab processing

    def process_state(self, state, nds='guess', multiprocess=True,
                      config=GWSummConfigParser(), datacache=None,
                      trigcache=None, plotqueue=None, segdb_error='raise'):
        """Process data for this tab in a given state
        """
        vprint("Processing '%s' state\n" % state.name)

        # flag those plots that were already written by this process
        for p in self.plots:
            if p.outputfile in globalv.WRITTEN_PLOTS:
                p.new = False

        # --------------------------------------------------------------------
        # process time-series

        # find channels that need a TimeSeries
        tschannels = self.get_channels('timeseries', 'spectrogram', 'spectrum',
                                       'histogram')
        if len(tschannels):
            vprint("    %d channels identified for TimeSeries\n"
                   % len(tschannels))
            get_timeseries_dict(tschannels, state, config=config, nds=nds,
                                multiprocess=multiprocess, cache=datacache,
                                return_=False)
            vprint("    All time-series data loaded\n")

        # find channels that need a StateVector
        svchannels = self.get_channels('statevector')
        if len(svchannels):
            vprint("    %d channels identified as StateVectors\n"
                   % len(svchannels))
            get_timeseries_dict(svchannels, state, config=config, nds=nds,
                                multiprocess=multiprocess, statevector=True,
                                cache=datacache, return_=False)
            vprint("    All state-vector data loaded\n")

        # --------------------------------------------------------------------
        # process spectrograms

        # find FFT parameters
        try:
            fftparams = dict(config.nditems('fft'))
        except NoSectionError:
            fftparams = {}

        for channel in self.get_channels('spectrogram', 'spectrum'):
            get_spectrogram(channel, state, config=config, return_=False,
                            multiprocess=multiprocess, **fftparams)

        # --------------------------------------------------------------------
        # process spectra

        for channel in self.get_channels('spectrum'):
            get_spectrum(channel, state, config=config, return_=False,
                         **fftparams)

        # --------------------------------------------------------------------
        # process segments

        # find flags that need a DataQualityFlag
        dqflags = self.get_flags('segments')
        if len(dqflags):
            vprint("    %d data-quality flags identified for SegDB query\n"
                   % len(dqflags))
            get_segments(dqflags, state, config=config, segdb_error=segdb_error)

        # --------------------------------------------------------------------
        # process triggers

        for etg, channel in self.get_triggers('triggers'):
            get_triggers(channel, etg, state.active, config=config,
                         cache=trigcache)

        # --------------------------------------------------------------------
        # make plots

        vprint("    Plotting... \n")

        # filter out plots that aren't for this state
        new_plots = [p for p in self.plots if
                     p.new and (p.state is None or p.state.name == state.name)]

        # process each one
        nproc = 0
        for plot in sorted(new_plots, key=lambda p: p._threadsafe and 1 or 2):
            globalv.WRITTEN_PLOTS.append(plot.outputfile)
            # queue plot for multiprocessing
            if (plotqueue and plot._threadsafe):
                process = Process(target=plot.queue, args=(plotqueue,))
                process.daemon = True
                process.start()
                nproc += 1
                sleep(0.5)
            # process plot now
            else:
                plot.process()
                vprint("        %s written\n" % plot.outputfile)
        if nproc:
            vprint("        %d plot processes queued.\n" % nproc)
        vprint("    Done.\n")

    # -------------------------------------------------------------------------
    # HTML operations

    def build_inner_html(self, state):
        """Write the '#main' HTML content for this tab.

        For now, this function just links all the plots in a 2-column
        format.
        """
        page = self.scaffold_plots(state)

        # link data
        page.hr(class_='row-divider')
        page.div(class_='row')
        page.div(class_='col-md-12')
        channels = self.get_channels('timeseries', 'statevector', 'spectrum',
                                     'spectrogram', new=False)
        if len(channels):
            page.h1('Channel information')
            page.add("The following channels were used to generate the above "
                     "data")
            headers = ['Channel', 'Type', 'Sample rate', 'Units']
            data = []
            for channel in channels:
                channel = get_channel(channel)
                # format CIS url and type
                if re.search('.[a-z]+\Z', channel.name):
                    name, ctype = channel.name.rsplit('.', 1)
                    c2 = get_channel(name)
                    ctype = ctype in ['rms'] and ctype.upper() or ctype.title()
                else:
                    c2 = channel
                    ctype = 'Raw'
                if c2.url:
                    link = html.markup.oneliner.a(str(channel),
                                                  href=c2.url,
                                                  target='_blank')
                else:
                    link = str(channel)

                # format sameple rate
                if (channel.sample_rate is not None and
                        isclose(channel.sample_rate.value, 1/60.)):
                    rate = '1/60 %s' % channel.sample_rate.unit
                else:
                    rate = str(channel.sample_rate)
                # format unit
                if channel.unit:
                    unit = str(channel.unit)
                else:
                    unit = 'Unknown'
                data.append([link, ctype, rate, unit])
            page.add(str(html.data_table(headers, data, table='data')))

        flags = self.get_flags('segments')
        if len(flags):
            page.h1('Data-quality flag information')
            page.add("The following data-quality flags were used to generate "
                     "the above data. This list does not include state "
                     "information")
            # make summary table
            headers = ['IFO', 'Name', 'Version', 'Defined duration',
                       'Active duration']
            data = []
            pc = float(abs(state.active) / 100.)
            for flag in flags:
                flag = get_segments(flag, state.active, query=False).copy()
                v = flag.version and str(flag.version) or ''
                try:
                    valid = '%.2f (%.2f%%)' % (abs(flag.valid),
                                               abs(flag.valid) / pc)
                except ZeroDivisionError:
                    valid = '0.00 (0.00%)'
                    active = '0.00 (0.00%)'
                else:
                    active = '%.2f (%.2f%%)' % (abs(flag.active),
                                                abs(flag.active) / pc)
                data.append([flag.ifo, flag.tag, v, valid, active])
            page.add(str(html.data_table(headers, data, table='data')))
            # print segment lists
            page.div(class_='panel-group', id="accordion")
            for i, flag in enumerate(flags):
                flag = get_segments(flag, state.active, query=False).copy()
                n = flag.name
                page.div(class_='panel panel-default')
                page.a(href='#flag%d' % i, **{'data-toggle': 'collapse',
                                              'data-parent': '#accordion'})
                page.div(class_='panel-heading')
                page.h4(n, class_='panel-title')
                page.div.close()
                page.a.close()
                page.div(id_='flag%d' % i, class_='panel-collapse collapse')
                page.div(class_='panel-body')
                # write segment summary
                page.p('This flag was defined and had a known state during '
                       'the following segments:')
                segwizard = StringIO()
                flag.valid.write(segwizard, format='segwizard')
                page.pre(segwizard.getvalue())
                segwizard.close()
                # write segment table
                page.p('This flag was active during the following segments:')
                segwizard = StringIO()
                flag.write(segwizard, format='segwizard')
                page.pre(segwizard.getvalue())
                segwizard.close()
                page.div.close()
                page.div.close()
                page.div.close()
            page.div.close()
        page.div.close()
        page.div.close()
        return page

    # -------------------------------------------------------------------------
    # methods

    def get_channels(self, *types, **kwargs):
        """Return the `set` of data channels required for plots of the
        given ``types``.

        Parameters
        ----------
        *types : `list` of `str`
            `list` of plot type strings whose channel sets to return
        new : `bool`, default: `True`
            only include plots whose 'new' attribute is True

        Returns
        -------
        channels : `list`
            an alphabetically-sorted `list` of channels
        """
        isnew = kwargs.pop('new', True)
        for key in kwargs:
            raise TypeError("'%s' is an invalid keyword argument for "
                            "this function")
        out = set()
        for plot in self.plots:
            if plot.type in types and (isnew and plot.new or not isnew):
                out.update(plot.channels)
        return sorted(out, key=lambda ch: ch.name)

    def get_flags(self, *types):
        """Return the `set` of data-quality flags required for plots of the
        given ``types``.

        Parameters
        ----------
        *types : `list` of `str`
            `list` of plot type strings whose flag sets to return

        Returns
        -------
        flags : `list`
            an alphabetically-sorted `list` of flags
        """
        out = set()
        for plot in self.plots:
            if plot.type in types and plot.new:
                out.update([f for cflag in plot.flags for f in
                            re_flagdiv.split(cflag)[::2] if f])
        return sorted(out, key=lambda dqf: str(dqf))

    def get_triggers(self, *types):
        """Return the `set` of data-quality flags required for plots of the
        given ``types``.

        Parameters
        ----------
        *types : `list` of `str`
            `list` of plot type strings whose flag sets to return

        Returns
        -------
        flags : `list`
            an alphabetically-sorted `list` of flags
        """
        out = set()
        for plot in self.plots:
            if plot.type in types and plot.new:
                for channel in plot.channels:
                    out.add((plot.etg, channel))
        return sorted(out, key=lambda ch: ch[1].name)

register_tab(SimpleStateTab)


class AboutTab(Tab):
    type = 'about'

    @staticmethod
    def build_inner_html(config=None):
        return html.about_this_page(config=config)

register_tab(AboutTab)
