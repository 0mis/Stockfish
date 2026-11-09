"""Frozen quantized coordinate-alignment screen. No chess positions or engines.

Credits: Shawn Xu/Stockfish checkpoint authors; the existing public paired-lane
reader by 0mis. Wortsman et al.(2022) and Ainsworth et al.(2023) motivate the
screen only. Geometry compatibility is not evidence of trained-coordinate
alignment or playing strength. GPL-3.0-or-later, like the inherited reader.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import hashlib
import importlib.util
import itertools
import json
import math
import mmap
import os
from pathlib import Path
import struct
import sys
import time
import urllib.request
import numpy as np

HERE=Path(__file__).resolve().parent


def need(value,message):
    if not value:raise ValueError(message)


def sha(raw):return hashlib.sha256(raw).hexdigest()
def emit(path,value):
    raw=(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    with path.open('xb') as out:out.write(raw)
    return {'file':path.name,'bytes':len(raw),'sha256':sha(raw)}


def parser(plan):
    source=HERE.parent/'layout/permute_nnue.py';pin=plan['source']['audited_parser'];raw=source.read_bytes()
    need(len(raw)==pin['bytes'] and sha(raw)==pin['sha256'],'Audited reader source changed')
    spec=importlib.util.spec_from_file_location('frozen_public_paired_reader',source)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module


def download(pin,destination):
    need(not destination.exists(),'Input destination exists')
    request=urllib.request.Request(pin['url'],headers={'User-Agent':'0mis-quantized-weight-pilot'})
    total=0;digest=hashlib.sha256()
    with urllib.request.urlopen(request,timeout=60) as response,destination.open('xb') as out:
        need(response.status==200,'Network input unavailable')
        final_url=response.geturl()
        while block:=response.read(1024*1024):
            total+=len(block);need(total<=pin['bytes'],'Input exceeds pinned length');digest.update(block);out.write(block)
    need(total==pin['bytes'] and digest.hexdigest()==pin['sha256'],'Original checkpoint length/fullSHA differs')
    return {'requested_url':pin['url'],'resolved_url':final_url,'bytes':total,'sha256':digest.hexdigest()}


def eligible_feature_rows(name,rows):
    """Structural feature geometry only: no checkpoint values or board corpus."""
    if name=='PP_3Wide':
        keep=[]
        for hi in range(96):
            for lo in range(hi):
                s,t=8+hi%48,8+lo%48
                if s!=t and abs(s%8-t%8)<=1:keep.append(hi*(hi-1)//2+lo)
        return keep
    if name.startswith('K32H3_block'):
        block=int(name[-1]);keep=[]
        for index in range(rows):
            king_bucket,local=divmod(index,704);piece,square=divmod(local,64)
            king_rank=7-king_bucket//4;king_file=7-king_bucket%4;king_square=king_rank*8+king_file
            if piece<2 and square//8 in (0,7):continue
            if block==0 and piece in (6,7,8,9):continue
            if block==1 and piece==9:continue
            if piece!=10 and square==king_square:continue
            if piece==10 and square!=king_square and max(abs(square//8-king_rank),abs(square%8-king_file))<=1:continue
            keep.append(index)
        return keep
    return list(range(rows))


def sample_rows(name,rows,count):
    candidates=eligible_feature_rows(name,rows)
    return np.asarray(sorted(candidates,key=lambda i:hashlib.sha256(('qinterp-alignment-v1|'+name+'|'+str(i)).encode('ascii')).digest())[:min(len(candidates),count)],dtype=np.int64)


def conditional_corr(a,b,minimum_common,minimum_std):
    """All centered column correlations on their joint NONZERO support.

    Six float64 GEMMs accumulate exact integer sums for the int8 weights.
    Constant vectors, all shared zeros and padding cannot yield a finite score.
    """
    a=np.asarray(a,dtype=np.float64);b=np.asarray(b,dtype=np.float64)
    need(a.ndim==b.ndim==2 and len(a)==len(b),'Correlation shape differs')
    ma=(a!=0).astype(np.float64);mb=(b!=0).astype(np.float64)
    n=ma.T@mb;safe=np.maximum(n,1)
    sx=a.T@mb;sy=ma.T@b
    vx=np.maximum((a*a).T@mb-sx*sx/safe,0)
    vy=np.maximum(ma.T@(b*b)-sy*sy/safe,0)
    covariance=a.T@b-sx*sy/safe
    informative=(n>=minimum_common)&(vx/safe>=minimum_std**2)&(vy/safe>=minimum_std**2)
    corr=np.full(n.shape,np.nan)
    denominator=np.sqrt(vx*vy)
    np.divide(covariance,denominator,out=corr,where=informative&(denominator>0))
    np.clip(corr,-1,1,out=corr)
    return corr,n


def number(value):return float(value) if np.isfinite(value) else None
def vector(values):return [number(value) for value in values]


def assignment(scores):
    """Exact optimum, diagnostic only. Never transform either checkpoint."""
    if len(scores)<=8:
        best=max(itertools.permutations(range(len(scores))),key=lambda order:sum(scores[i,j] for i,j in enumerate(order)))
        return np.arange(len(scores)),np.asarray(best)
    from scipy.optimize import linear_sum_assignment
    return linear_sum_assignment(scores,maximize=True)


def paired_report(direct,swapped,gate,minimum_alternatives):
    half=len(direct);identity=np.diag(direct);supported=np.isfinite(identity);indices=np.flatnonzero(supported)
    combined=np.fmax(direct,swapped)
    alternative=combined.copy();np.fill_diagonal(alternative,np.diag(swapped))
    counts=np.sum(np.isfinite(alternative),axis=1)
    alt=np.max(np.where(np.isfinite(alternative),alternative,-2),axis=1)
    margins=identity-alt
    if len(indices):
        square=combined[np.ix_(indices,indices)];rows,columns=assignment(np.where(np.isfinite(square),square,-2))
        chosen=indices[columns];chosen_swaps=np.asarray([np.isfinite(swapped[i,j]) and (not np.isfinite(direct[i,j]) or swapped[i,j]>direct[i,j]) for i,j in zip(indices[rows],chosen)])
        match=(indices[rows]==chosen)&~chosen_swaps
        gain=float(np.mean(square[rows,columns])-np.mean(identity[indices]))
        median=float(np.median(identity[indices]));wins=float(np.mean((margins[indices]>=gate['minimum_identity_margin'])&(counts[indices]>=minimum_alternatives)))
        assignment_fraction=float(np.mean(match))
    else:
        median=wins=assignment_fraction=gain=None;chosen=np.asarray([],dtype=int);chosen_swaps=[]
    conditions={'supported_pair_fraction':float(np.mean(supported))>=gate['minimum_supported_pair_fraction'],
        'median_identity_correlation':median is not None and median>=gate['minimum_median_identity_correlation'],
        'strict_identity_winner_fraction':wins is not None and wins>=gate['minimum_fraction_strict_identity_winner'],
        'assignment_identity_fraction':assignment_fraction is not None and assignment_fraction>=gate['minimum_assignment_identity_fraction'],
        'assignment_mean_gain':gain is not None and gain<=gate['maximum_assignment_mean_gain']+1e-12}
    return {'pass':all(conditions.values()),'conditions':conditions,'supported_pairs':int(sum(supported)),'total_pairs':half,
        'median_identity_correlation':median,'strict_identity_winner_fraction':wins,'assignment_identity_fraction':assignment_fraction,
        'best_assignment_mean_gain_over_direct_identity':gain,'identity_correlations':vector(identity),
        'identity_minus_best_other_margins':vector(margins),'informative_alternative_counts':counts.astype(int).tolist(),
        'supported_pair_indices':indices.tolist(),'best_assignment_targets':chosen.tolist(),'best_assignment_factor_swaps':[bool(x) for x in chosen_swaps],
        'permutation_applied':False}


def stack_views(data,layout,index,reader):
    spec=layout.spec;start=layout.stacks_start+index*spec.stack_bytes;cursor=start
    need(struct.unpack_from('<I',data,cursor)[0]==reader.STACK_TAG,'Head tag differs');cursor+=4
    arrays={};padding=[]
    for name,outputs,inputs in (('fc0',spec.fc0,spec.lanes),('fc1',spec.fc1,2*spec.fc0),('fc2',1,2*spec.fc0+2*spec.fc1)):
        bias=np.frombuffer(data,'<i4',outputs,cursor);cursor+=outputs*4
        stride=reader._padded(inputs);weights=np.frombuffer(data,'i1',outputs*stride,cursor).reshape(outputs,stride);cursor+=outputs*stride
        arrays[name]={'bias':bias,'weights':weights[:,:inputs]};padding.append(weights[:,inputs:].tobytes())
        bias.flags.writeable=False;weights.flags.writeable=False
    need(cursor==start+spec.stack_bytes,'Every head parameter/EOF boundary differs')
    return arrays,{'logical_parameters':sum(v['bias'].size+v['weights'].size for v in arrays.values()),
        'padding_bytes':sum(map(len,padding)),'padding_sha256':sha(b''.join(padding))}


def aligned_vector(a,b,minimum,minimum_std):
    corr,n=conditional_corr(np.asarray(a).reshape(-1,1),np.asarray(b).reshape(-1,1),minimum,minimum_std)
    return {'correlation':number(corr[0,0]),'common_nonzero':int(n[0,0]),'logical_count':int(np.size(a))}


def head_report(a,b,plan):
    gate=plan['head_gate'];cfg=plan['correlation'];report={};passes=[]
    for name,minimum in (('fc0',cfg['fc0_row_minimum_common_nonzero']),('fc1',cfg['fc1_row_minimum_common_nonzero']),('fc2',cfg['fc2_minimum_common_nonzero'])):
        x,y=a[name]['weights'],b[name]['weights'];flat=aligned_vector(x,y,gate[name+'_flat_min_common'],cfg['minimum_quantized_std'])
        result={'flattened':flat,'pass':flat['correlation'] is not None and flat['correlation']>=gate['minimum_flattened_layer_correlation']}
        if name!='fc2':
            corr,n=conditional_corr(x.T,y.T,minimum,cfg['minimum_quantized_std']);diagonal=np.diag(corr);valid=np.isfinite(diagonal)
            median=float(np.median(diagonal[valid])) if any(valid) else None
            fraction=float(np.mean(diagonal[valid]>=0.75)) if any(valid) else None
            best=np.argmax(np.where(np.isfinite(corr),corr,-2),axis=1)
            result.update(informative_rows=int(sum(valid)),total_rows=len(valid),row_identity_correlations=vector(diagonal),
                row_common_nonzero=np.diag(n).astype(int).tolist(),row_median_identity_correlation=median,
                row_fraction_at_least_075=fraction,row_best_identity_fraction=float(np.mean(best[valid]==np.flatnonzero(valid))) if any(valid) else None,
                fixed_skip_rows=[{'row':i,'identity_correlation':number(diagonal[i])} for i in (30,31)] if name=='fc0' and len(diagonal)==32 else [])
            result['pass']&=(float(np.mean(valid))>=gate['minimum_informative_row_fraction'] and median is not None
                and median>=gate['minimum_row_median_identity_correlation'] and fraction>=gate['minimum_row_fraction_at_least_075'])
        bias_a=a[name]['bias'].astype(np.int64);bias_b=b[name]['bias'].astype(np.int64)
        result['bias_diagnostics']={'signed_A':bias_a.tolist(),'signed_B':bias_b.tolist(),'maximum_absolute_delta':int(np.max(np.abs(bias_b-bias_a))),
            'informative_centered_comparison':aligned_vector(bias_a,bias_b,8,cfg['minimum_quantized_std']) if len(bias_a)>1 else None,
            'bias_used_as_alignment_gate':False,'scalar_is_not_a_correlation':len(bias_a)==1}
        report[name]=result;passes.append(bool(result['pass']))
    return {'pass':all(passes),'layers':report}


def main():
    args=argparse.ArgumentParser();args.add_argument('--plan',type=Path,default=HERE/'plan.json');args.add_argument('--expected-plan-sha',required=True);args.add_argument('--work',type=Path,required=True);args=args.parse_args()
    raw=args.plan.read_bytes();need(sha(raw)==args.expected_plan_sha,'Precommitted plan differs');plan=json.loads(raw)
    work=args.work.resolve();need(not work.exists(),'Fresh owned evidence directory required');work.mkdir(parents=True);inputs=work/'inputs';inputs.mkdir();evidence=work/'evidence';evidence.mkdir()
    started=time.monotonic();reader=parser(plan);receipts={}
    try:
        import scipy
        for key,pin in plan['networks'].items():receipts[key]=download(pin,inputs/pin['file'])
        emit(evidence/'INPUTS.json',{'plan_sha256':args.expected_plan_sha,'commit':os.environ.get('GITHUB_SHA'),'inputs':receipts,
            'parser_source':plan['source']['audited_parser'],'source':plan['source'],'numpy_version':np.__version__,'scipy_version':scipy.__version__,
            'no_positions_or_engines_in_alignment':True})
        with ExitStack() as contexts:
            data={}
            for key,pin in plan['networks'].items():
                stream=contexts.enter_context((inputs/pin['file']).open('rb'));data[key]=contexts.enter_context(mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ))
            layouts={key:reader.parse_layout(value) for key,value in data.items()}
            need(layouts['A']==layouts['B'],'Matching geometry/routing of every stored stack required')
            layout=layouts['A'];ft={key:reader.ft_views(value,layouts[key]) for key,value in data.items()}
            tables={key:(v.threat_weights,v.pair_weights,v.psq_weights) for key,v in ft.items()}
            planes={};directs=[];swaps=[];matrices={}
            for plane in plan['geometry']['planes']:
                indices=sample_rows(plane['name'],plane['rows'],plan['sampling']['samples_per_plane'])
                x,y=[tables[key][plane['table']][indices+plane['offset']].astype(np.float64) for key in ('A','B')]
                keep=(np.count_nonzero(x,axis=1)>=32)&(np.count_nonzero(y,axis=1)>=32)&(x.var(axis=1)>=0.25)&(y.var(axis=1)>=0.25)
                x,y=x[keep],y[keep]
                corr,n=conditional_corr(x,y,plan['correlation']['ft_minimum_common_nonzero'],plan['correlation']['minimum_quantized_std'])
                half=layout.spec.half;direct=(corr[:half,:half]+corr[half:,half:])/2;swapped=(corr[:half,half:]+corr[half:,:half])/2
                result=paired_report(direct,swapped,plan['ft_gate'],plan['correlation']['ft_minimum_informative_alternatives'])
                result.update(sampled_rows=len(indices),retained_rows=int(sum(keep)),sample_local_row_ids=indices.tolist(),retained_sample_mask=keep.tolist(),
                    structurally_eligible_rows=len(eligible_feature_rows(plane['name'],plane['rows'])),
                    sample_indices_i64le_sha256=sha(indices.astype('<i8').tobytes()))
                result['conditions']['retained_sample_support']=int(sum(keep))>=plan['sampling']['minimum_retained_rows'];result['pass']=all(result['conditions'].values())
                planes[plane['name']]=result;directs.append(direct);swaps.append(swapped)
                matrices[plane['name']+'_direct']=direct;matrices[plane['name']+'_swapped']=swapped
                print(json.dumps({'phase':'plane_measured','plane':plane['name'],'supported':result['supported_pairs'],'median':result['median_identity_correlation'],'identity_fraction':result['assignment_identity_fraction'],'pass':result['pass']}),flush=True)
            aggregate=paired_report(np.mean(directs,axis=0),np.mean(swaps,axis=0),plan['ft_gate'],plan['correlation']['ft_minimum_informative_alternatives'])
            heads=[]
            for i in range(layout.spec.stored):
                aa,ma=stack_views(data['A'],layout,i,reader);bb,mb=stack_views(data['B'],layout,i,reader)
                result=head_report(aa,bb,plan);result.update(physical_head=i,A_geometry=ma,B_geometry=mb);heads.append(result)
            passed=all(v['pass'] for v in planes.values()) and aggregate['pass'] and all(v['pass'] for v in heads)
            result={'schema':'quantized-soup-alignment-result-v1','status':'MEASURED','decision':'ALIGNMENT_PASS_INTERPOLATION_PENDING' if passed else 'STOP_DIRECT_INTERPOLATION_LINE',
                'plan_sha256':args.expected_plan_sha,'source_commit':os.environ.get('GITHUB_SHA'),'network_inputs':receipts,
                'geometry':{'raw_routes':layout.spec.raw_routes,'physical_heads':layout.spec.stored,'ft_lanes':layout.spec.lanes,
                    'all_stack_tags_and_exact_EOF_verified':True,'fallback_u16le_sha256':layout.spec.fallback_sha256,'fallback':list(layout.fallback),'raw_to_physical':list(layout.raw_to_physical)},
                'feature_planes':planes,'aggregate_paired_lane_alignment':aggregate,'heads':heads,
                'heads_passing':sum(v['pass'] for v in heads),'head_layer_passing':{name:sum(v['layers'][name]['pass'] for v in heads) for name in ('fc0','fc1','fc2')},
                'candidate_created':False,'permutation_applied':False,'weights_fitted':False,'positions_or_labels_read':0,'native_processes':0,'games':0,
                'seconds':time.monotonic()-started,'official_superiority_claim':False,'motivation_only_papers':plan['motivation']}
            np.savez_compressed(evidence/'CORRELATION-MATRICES.npz',**matrices)
            emit(evidence/'RESULT.json',result)
            emit(evidence/'SUMMARY.json',{'decision':result['decision'],'heads_passing':result['heads_passing'],'head_layer_passing':result['head_layer_passing'],
                'planes':{name:{k:v[k] for k in ('pass','supported_pairs','median_identity_correlation','strict_identity_winner_fraction','assignment_identity_fraction','best_assignment_mean_gain_over_direct_identity')} for name,v in planes.items()},
                'aggregate':{k:aggregate[k] for k in ('pass','supported_pairs','median_identity_correlation','strict_identity_winner_fraction','assignment_identity_fraction','best_assignment_mean_gain_over_direct_identity')},
                'candidate_created':False,'native_processes':0,'games':0,'plan_sha256':args.expected_plan_sha,'source_commit':result['source_commit']})
            # Release all exported mmap views before closing their buffers.
            del ft,tables,aa,bb
        print(json.dumps({'phase':'alignment_complete','decision':result['decision'],'heads_passing':result['heads_passing']}),flush=True)
    except BaseException as error:
        if not (evidence/'FAILURE.json').exists():emit(evidence/'FAILURE.json',{'status':'INCOMPLETE','error':type(error).__name__,'message':str(error),'candidate_created':False,'native_processes':0,'games':0})
        raise


if __name__=='__main__':main()
