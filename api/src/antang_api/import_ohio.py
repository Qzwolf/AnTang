"""Local research import; no XML, PDFs or patient rows are sent to external services."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import secrets
from uuid import NAMESPACE_URL, uuid5
import xml.etree.ElementTree as ET


def timestamp(value):
    # Ohio XML uses day-month-year and shifted research dates, sometimes in the future.
    return datetime.strptime(value, '%d-%m-%Y %H:%M:%S').replace(tzinfo=timezone.utc)


def parse_files(paths, root):
    glucose, events, sources, rejected = {}, {}, [], []
    identity = None
    insulin_type = None
    for path in paths:
        content = path.read_bytes()
        if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
            raise ValueError('XML entities are not accepted')
        doc = ET.fromstring(content)
        if identity is not None and doc.get('id') != identity:
            raise ValueError('Mixed subjects')
        identity = doc.attrib['id']
        if insulin_type is not None and doc.get('insulin_type') != insulin_type:
            raise ValueError('Inconsistent insulin type')
        insulin_type = doc.get('insulin_type')
        ref = str(path.relative_to(root))
        sources.append({'file': ref, 'sha256': hashlib.sha256(content).hexdigest()})
        for section in doc:
            if section.tag not in {'glucose_level','bolus','basal','temp_basal','meal','finger_stick'}:
                continue
            for index, node in enumerate(section, 1):
                raw = dict(node.attrib)
                try:
                    start = timestamp(raw.get('ts', raw.get('ts_begin', '')))
                    end = timestamp(raw['ts_end']) if raw.get('ts_end') else None
                    value = float(raw.get('value', raw.get('dose', raw.get('carbs', ''))))
                    if not math.isfinite(value) or value < 0 or (end is not None and end < start):
                        raise ValueError('Invalid value or interval')
                    if section.tag == 'glucose_level':
                        if not value.is_integer() or not 20 <= value <= 600:
                            raise ValueError('Outside supported glucose range')
                        key = start.isoformat()
                        if key in glucose and glucose[key]['value_mg_dl'] != int(value):
                            raise RuntimeError('Conflicting CGM samples at the same timestamp')
                        glucose[key] = {'measured_at': start, 'value_mg_dl': int(value), 'source': 'ohio_cgm',
                            'context': 'other', 'note': f'OhioT1DM {ref}:glucose_level:{index}；日期已由原数据集脱敏平移；原时区未提供，UTC仅为存储约定。'}
                    else:
                        kind = section.tag
                        key = kind + ':' + json.dumps(raw, sort_keys=True)
                        unit = 'U/h' if kind in {'basal','temp_basal'} else 'U' if kind == 'bolus' else 'g' if kind == 'meal' else 'mg/dL'
                        events[key] = {'kind': kind, 'measured_at': start, 'ended_at': end, 'value': value,
                            'unit': unit, 'detail': raw.get('type', ''), 'source_ref': f'{ref}:{kind}:{index}', 'raw_record': raw}
                except (ValueError, KeyError):
                    rejected.append({'file': ref, 'kind': section.tag, 'index': index, 'reason': 'invalid_value_or_time'})
    if not glucose:
        raise ValueError('No valid CGM samples')
    begin = min(r['measured_at'] for r in glucose.values())
    finish = max(r['measured_at'] for r in glucose.values()) + timedelta(minutes=5)
    basal = sorted((row for row in events.values() if row['kind'] == 'basal'), key=lambda r: r['measured_at'])
    for index, row in enumerate(basal):
        row['ended_at'] = basal[index+1]['measured_at'] if index+1 < len(basal) else max(finish, row['measured_at'])
    return {'id': identity, 'insulin_type': insulin_type, 'start': begin, 'end': finish,
            'glucose': glucose, 'events': events, 'sources': sources, 'rejected': rejected}


async def apply_subject(session, username, name, dataset, insulin, begin, end, provenance):
    from sqlalchemy import select
    from antang_api.models import PersonalProfile, ProactiveCareSettings, User, UserRole
    from antang_api.models.study import StudySubject
    from antang_api.security import hash_password
    user = await session.scalar(select(User).where(User.username_normalized == username))
    if user is None:
        user = User(username=username, username_normalized=username, full_name=name,
                    password_hash=hash_password(secrets.token_urlsafe(48)), role=UserRole.PATIENT)
        session.add(user)
        await session.flush()
        session.add(PersonalProfile(user_id=user.id))
        session.add(ProactiveCareSettings(user_id=user.id))
    if user.full_name != name or user.role != UserRole.PATIENT:
        raise ValueError('Research account conflict')
    existing = await session.get(StudySubject, user.id)
    if existing is None:
        session.add(StudySubject(user_id=user.id, dataset=dataset, insulin_type=insulin,
            period_start=begin, period_end=end, provenance=provenance))
    elif existing.provenance != provenance:
        raise ValueError('Existing research source differs; refusing to overwrite')
    return user.id


async def insert_rows(session, model, rows):
    from sqlalchemy.dialects.postgresql import insert
    added = 0
    for offset in range(0, len(rows), 500):
        added += len(list(await session.scalars(insert(model).values(rows[offset:offset+500])
            .on_conflict_do_nothing(index_elements=['id']).returning(model.id))))
    return added


async def run(args):
    from antang_api.database import engine, session_factory
    from antang_api.models import GlucoseReading
    from antang_api.models.study import StudyEvent
    result = {}
    for year in ('2018','2020'):
        groups = {}
        for path in sorted((args.root/year).rglob('*.xml')):
            groups.setdefault(path.name.split('-')[0], []).append(path)
        for subject, paths in groups.items():
            data = parse_files(paths, args.root)
            if subject != data['id']:
                raise ValueError('Subject filename mismatch')
            key = f'ohio_{year}_{subject}'
            report = {'cgm': len(data['glucose']), 'events': len(data['events']), 'rejected': data['rejected'],
                      'start': data['start'].isoformat(), 'end': data['end'].isoformat()}
            if args.apply:
                async with session_factory() as session, session.begin():
                    user_id = await apply_subject(session, key, f'研究数据 · Ohio {year} · {subject}', 'OhioT1DM',
                        data['insulin_type'], data['start'], data['end'], {'files': data['sources'],
                        'notice': '仅限本地研究；原日期已脱敏平移，可能处于未来；原时区未知，UTC仅为存储约定。体重99为占位值，未导入。',
                        'citation': 'Marling & Bunescu, OhioT1DM Update 2020'})
                    cgm = [dict(row, user_id=user_id, id=uuid5(NAMESPACE_URL,key+':cgm:'+identity)) for identity,row in data['glucose'].items()]
                    events = [dict(row,user_id=user_id,id=uuid5(NAMESPACE_URL,key+':event:'+identity)) for identity,row in data['events'].items()]
                    report['added_cgm'] = await insert_rows(session, GlucoseReading, cgm)
                    report['added_events'] = await insert_rows(session, StudyEvent, events)
                    report['patient_id'] = str(user_id)
            result[key] = report
    if args.reports:
        for report in json.loads(args.reports.read_text()):
            key = 'cgm_report_' + Path(report['file']).stem
            if not report['start'] or not report['end']:
                raise ValueError('Report date missing')
            begin = datetime.fromisoformat(report['start']).replace(tzinfo=timezone.utc)
            end = datetime.fromisoformat(report['end']).replace(tzinfo=timezone.utc)+timedelta(days=1)
            if args.apply:
                async with session_factory() as session, session.begin():
                    await apply_subject(session,key,'CGM报告样本 · '+Path(report['file']).stem,'CGM PDF',None,begin,end,
                        {'report': report, 'notice': '用户提供的本地报告摘要；保留原报告统计及单位，不从图像反推逐点血糖，不导入姓名、电话或设备编号。'})
            result[key] = {'report_summary': True, 'start': begin.isoformat(), 'end': end.isoformat()}
    await engine.dispose()
    if args.receipt:
        args.receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'subjects': len(result), 'cgm': sum(r.get('cgm',0) for r in result.values()),
                      'events': sum(r.get('events',0) for r in result.values()),
                      'rejected': sum(len(r.get('rejected',[])) for r in result.values()),
                      'added_cgm': sum(r.get('added_cgm',0) for r in result.values()),
                      'added_events': sum(r.get('added_events',0) for r in result.values())}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--reports',type=Path)
    parser.add_argument('--receipt',type=Path)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
