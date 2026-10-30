"""Import attributed UCI historical glucose into isolated public example accounts."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import secrets
import subprocess
import tarfile
from uuid import NAMESPACE_URL, uuid5
import zipfile

SHA256 = 'dd5c95c4f125222d894a9478ee8d63983bc32581d53d79d02f1057ab1bcd39a9'
CONTEXTS = {48: 'other', 57: 'other', 58: 'pre_meal', 59: 'post_meal',
            60: 'pre_meal', 61: 'post_meal', 62: 'pre_meal', 63: 'post_meal', 64: 'pre_meal'}


def parse_subject(subject: str, content: str) -> tuple[list[dict], list[dict]]:
    records, rejected = [], []
    for line, raw in enumerate(content.splitlines(), 1):
        fields = raw.split()
        if len(fields) != 4:
            rejected.append({'subject': subject, 'line': line, 'reason': 'invalid_fields'})
            continue
        date, clock, code_text, value_text = fields
        try:
            code = int(code_text)
        except ValueError:
            rejected.append({'subject': subject, 'line': line, 'reason': 'invalid_code'})
            continue
        if code not in CONTEXTS:
            continue
        try:
            measured = datetime.strptime(date + ' ' + clock, '%m-%d-%Y %H:%M').replace(tzinfo=timezone.utc)
            value = int(value_text)
            if not 20 <= value <= 600:
                raise ValueError('outside supported range')
        except ValueError:
            rejected.append({'subject': subject, 'line': line, 'reason': 'invalid_glucose_or_time'})
            continue
        records.append({
            'id': uuid5(NAMESPACE_URL, f'uci-diabetes:{SHA256}:{subject}:{line}'),
            'value_mg_dl': value, 'measured_at': measured, 'context': CONTEXTS[code],
            'source': 'public_dataset',
            'note': f'UCI Diabetes 公开历史数据 data-{subject}:{line}; code={code}; CC BY 4.0; doi:10.24432/C5T59G。原时区未知，UTC仅为存储约定；部分纸质时间为餐次指定。',
        })
    return records, rejected


def load_archive(path: Path, subjects: list[str]) -> dict:
    if hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Archive checksum mismatch')
    with zipfile.ZipFile(path) as archive:
        packed = archive.read('diabetes-data.tar.Z')
    unpacked = subprocess.run(['gzip', '-dc'], input=packed, capture_output=True, check=True).stdout
    result = {}
    with tarfile.open(fileobj=io.BytesIO(unpacked)) as archive:
        for subject in subjects:
            member = archive.extractfile(f'Diabetes-Data/data-{subject}')
            if member is None:
                raise ValueError('Missing subject ' + subject)
            result[subject] = parse_subject(subject, member.read().decode('ascii'))
    return result


async def import_records(data: dict) -> dict:
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert
    from antang_api.database import engine, session_factory
    from antang_api.models import GlucoseReading, PersonalProfile, ProactiveCareSettings, User, UserRole
    from antang_api.security import hash_password

    summary = {}
    async with session_factory() as session, session.begin():
        for subject, (records, rejected) in data.items():
            if not records:
                raise ValueError('No valid glucose for subject ' + subject)
            username = 'uci_diabetes_' + subject
            name = '公开数据集示例患者 · UCI ' + subject
            user = await session.scalar(select(User).where(User.username_normalized == username))
            if user is None:
                user = User(username=username, username_normalized=username, full_name=name,
                            password_hash=hash_password(secrets.token_urlsafe(48)), role=UserRole.PATIENT)
                session.add(user)
                await session.flush()
            if user.full_name != name or user.role != UserRole.PATIENT:
                raise ValueError('Example account identity conflict')
            if await session.get(PersonalProfile, user.id) is None:
                session.add(PersonalProfile(user_id=user.id))
            if await session.get(ProactiveCareSettings, user.id) is None:
                session.add(ProactiveCareSettings(user_id=user.id))
            rows = [dict(record, user_id=user.id) for record in records]
            added = len(list((await session.scalars(insert(GlucoseReading).values(rows)
                .on_conflict_do_nothing(index_elements=['id']).returning(GlucoseReading.id))).all()))
            saved = list(await session.scalars(select(GlucoseReading).where(
                GlucoseReading.user_id == user.id, GlucoseReading.source == 'public_dataset')))
            assert len(saved) == len(records), 'Imported count mismatch'
            summary[subject] = {'patient_id': str(user.id), 'username': username, 'count': len(saved),
                                'added': added, 'rejected': rejected,
                                'start': min(r['measured_at'] for r in records).isoformat(),
                                'end': max(r['measured_at'] for r in records).isoformat()}
    await engine.dispose()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--subjects', nargs='+', default=['01', '02', '03'])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if len(set(args.subjects)) != len(args.subjects) or any(s not in {f'{i:02}' for i in range(1, 71)} for s in args.subjects):
        parser.error('Subjects must be distinct numbers 01–70')
    data = load_archive(args.archive, args.subjects)
    result = asyncio.run(import_records(data)) if args.apply else {
        subject: {'valid': len(records), 'rejected': rejected} for subject, (records, rejected) in data.items()}
    print(json.dumps({'applied': args.apply, 'sha256': SHA256, 'subjects': result}, ensure_ascii=False))


if __name__ == '__main__':
    main()
