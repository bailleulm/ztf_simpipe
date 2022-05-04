from optparse import OptionParser
from ztf_pipeutils.ztf_util import make_dict_from_config, make_dict_from_optparse
import ztf_simfit_input as simfit_input
from ztf_pipeutils.ztf_util import multiproc, dump_in_yaml, checkDir
from ztf_pipeutils.ztf_hdf5 import Write_LightCurve
from ztf_simfit.ztf_simu import Simul_lc
import pandas as pd
import os
import copy
import numpy as np
import numpy.lib.recfunctions as rf
import operator as op


def loadData(theDir, theFile):
    """
    function to load data

    Parameters
    ---------------
    theDir: str
      location dir of the data
    theFile: str
      name of the file to load

    Returns
    ----------
    pandas df of the data


    """

    fullPath = os.path.join(theDir, theFile)
    data = pd.read_hdf(fullPath)

    return data


def simu(hpixels, params, j=0, output_q=None):
    """
    method to perform simulations on multiple healpixIDs

    Parameters
    --------------
    hpixels: array
      list of (pixnum, season) to simulate
    params: dict
      dict of parameters
    j: int, opt
      internal tag for multiprocessing (proc number) (default: 0)
    output_q: multiprocessing queue,opt
     (default: None)

    Returns
    -----------
    liste of (lc,metadata)

    """
    params['seed'] += j
    obs = params['obsData']
    del params['obsData']
    outputDir = params['outputDirSimu']
    lcName = params['lcName']
    metaName = params['metaName']
    path_prefix = params['path_prefix']
    cadData = params['cadData']
    runtype = params['runtype']

    # set the night col here
    obs['night'] = obs['time']-obs['time'].min()
    obs['night'] = obs['night'].astype(int)

    # lc simulation
    simlc = Simul_lc(**params)

    rlc = []
    rmeta = []
    rmeta_rej = []
    for vv in hpixels:
        healpixID = vv[0]
        season = vv[1]
        lc, metadata, meta_rej = processPixel(
            obs, simlc, cadData, healpixID, season)
        if runtype == 'indiv':
            writeLC([lc], [metadata], [meta_rej], healpixID, season,
                    outputDir, lcName, metaName, path_prefix)
        else:
            # r.append((lc, metadata, meta_rej))
            rlc.append(lc)
            rmeta.append(metadata)
            rmeta_rej.append(meta_rej)

    if output_q is not None:
        return output_q.put({j: (rlc, rmeta, rmeta_rej)})
    else:
        return 0


def processPixel(obs, simlc, cadData, healpixID, season):

    idx = cadData['healpixID'] == healpixID
    idx &= cadData['season'] == season
    selcad = cadData[idx]
    lc = lcPixel(selcad.iloc[0], obs, simlc)

    coldict = dict(zip(['healpixID', 'season'], [healpixID, season]))
    metadata = None
    meta_rejected = None

    if lc is None:
        return lc, metadata, meta_rejected

    if lc.meta is not None:
        metadata = add_cols(lc.meta, coldict)

    if lc.meta_rejected is not None:
        meta_rejected = add_cols(lc.meta_rejected, coldict)

    return lc, metadata, meta_rejected


def writeLC(lc, metadata, meta_rejected, healpixID, season, outputDir, lcName, metaName, path_prefix):

    lcName = lcName.replace(
        '.hdf5', '_{}_{}.hdf5'.format(healpixID, season))
    metaName = metaName.replace(
        '.hdf5', '_{}_{}.hdf5'.format(healpixID, season))

    # write LC and metadata
    Write = Write_LightCurve(
        outputDir=outputDir, file_data=lcName, file_meta=metaName, path_prefix=path_prefix)

    path = '_{}_{}'.format(healpixID, season)
    Write.write_data(lc, metadata, meta_rejected, path=path)


def add_cols(arro, dictcol={'dummy': 0}):
    """
    Function to add columns to an array

    Parameters
    --------------
    arro: array
      original array
    dictcol: dict, opt
    colums to add to arr0

    Returns
    ----------
    array with appended cols


    """
    arr = np.copy(arro)
    for key, val in dictcol.items():
        arr = rf.append_fields(arr, key, [val]*len(arr), usemask=False)

    return arr


def lcPixel(selcad, obsData, simlc):
    """
    Function to process (simul LC on) a pixel

    Parameters
    -------------
    selcad: array
      cadence data
    obsData: array
      observations
    simlc: Simul_lc instance

    Returns
    ----------
    lc from simsurvey

    """
    hpix = int(selcad['healpixID'])
    time_min = selcad['time_min']
    time_max = selcad['time_max']
    healpixRA = selcad['healpixRA']
    healpixDec = selcad['healpixDec']
    # obsData['healpixID'] = obsData['healpixID'].apply(
    #    lambda x: x.split(','))
    # select obs corresponding to (time_min, time_max)
    idx = obsData['time'] >= time_min
    idx &= obsData['time'] <= time_max
    seldata = obsData[idx]
    seldata.loc[:, 'healpixID_new'] = seldata.loc[:,
                                                  'healpixID'].str.split(',')
    seldata = seldata.loc[seldata.apply(
        lambda x: getobs(x, 'healpixID_new', hpix), axis=1)]

    ra_range = (healpixRA, healpixRA)
    dec_range = (healpixDec, healpixDec)
    seldata['ra'] = healpixRA
    seldata['dec'] = healpixDec
    # lc simulation - if enough data
    lc = None
    # coaddition here
    seldata['healpixID'] = hpix
    seldata = seldata.groupby(
        ['night', 'band', 'field', 'rcid']).mean().reset_index()
    if len(seldata) > 5:
        lc = simlc(seldata, ra_range, dec_range)
    return lc


def getobs(grp, col, hpix):
    """
    Function to select a grp from a comparison of sets

    Parameters
    --------------
    grp: pandas group
      data to process
    col: str
      colname of the grp
    hpix: int
      value to compare with

    Returns
    ----------
    True if intersection found, false otherwise

    """
    hpix = set([str(hpix)])
    ccp = set(grp[col])
    #print(ccp, hpix, bool(ccp & hpix))

    return bool(ccp & hpix)


# get all possible simulation parameters and put in a dict
path = list(simfit_input.__path__)
confDict = make_dict_from_config(path[0], 'config_simu_pixels.txt')

parser = OptionParser()
# parser : 'dynamical' generation
for key, vals in confDict.items():
    vv = vals[1]
    if vals[0] != 'str':
        vv = eval('{}({})'.format(vals[0], vals[1]))
    parser.add_option('--{}'.format(key), help='{} [%default]'.format(
        vals[2]), default=vv, type=vals[0], metavar='')

opts, args = parser.parse_args()

# load the new values
params = {}
for key, vals in confDict.items():
    newval = eval('opts.{}'.format(key))
    # params[key] = (vals[0], newval)
    params[key] = newval
print(params)

if params['ztfdataDir'] == 'default':
    import ztf_data as ztf_data
    path = list(ztf_data.__path__)
    params['ztfdataDir'] = path[0]

ntransient = params['ntransient']
nproc = params['nprocSimu']
params['z_range'] = (params['zmin'], params['zmax'])
outputDir = params['outputDirSimu']
lcName = params['lcName']
metaName = params['metaName']
path_prefix = params['path_prefix']
pixelList = []
if opts.pixelList != 'all':
    pixelList = list(map(int, opts.pixelList.split(',')))
npixels = opts.npixels


# dump script parameters in yaml file
checkDir(outputDir)
nameOut = metaName.replace('.hdf5', '.yaml')
dump_in_yaml(opts, confDict, outputDir, nameOut, 'simu')

# load observations
cadData = loadData(opts.cadDir, opts.cadFile)

# load observations
obsData = loadData(opts.obsDir, opts.obsFile)

# print(cadData)
# print(obsData)

params['obsData'] = obsData
params['cadData'] = cadData

if pixelList:
    idx = cadData['healpixID'].isin(pixelList)
    cadData = cadData[idx]

if npixels > 0:
    cadData = cadData.sample(n=npixels)

if __name__ == '__main__':
    ffi = np.unique(cadData[['healpixID', 'season']].to_records(index=False))
    if opts.runtype == 'indiv':
        to = list(map(lambda *x: x, *([iter(ffi)] * nproc)))
        for tt in to:
            lc_dict = multiproc(tt, params, simu, nproc, gather=False)
    else:
        lc_dict = multiproc(ffi, params, simu, nproc, gather=False)

        # write LC and metadata
        Write = Write_LightCurve(
            outputDir=outputDir, file_data=lcName, file_meta=metaName, path_prefix=path_prefix)
        rlc = []
        rmeta = []
        rmeta_rej = []
        for i, lcl in lc_dict.items():
            for ll in lcl[0]:
                rlc.append(ll)
            for ll in lcl[1]:
                rmeta.append(ll)
            for ll in lcl[2]:
                rmeta_rej.append(ll)

        data = Write.write_data(rlc, rmeta, rmeta_rej)
