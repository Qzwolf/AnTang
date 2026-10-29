from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from antang_api.import_ohio import parse_files
from antang_api.models import User, UserRole
from antang_api.models.study import StudyEvent, StudySubject
from antang_api.patient_access import set_user_role


def test_ohio_parser_keeps_cgm_dose_rate_zero_and_shifted_dates(tmp_path):
    path = tmp_path/'559.xml'
    xml = '''<patient id="559" weight="99" insulin_type="Novalog"><glucose_level>
    <event ts="19-05-2027 01:00:00" value="101"/><event ts="19-05-2027 01:05:00" value="102"/>
    </glucose_level><basal><event ts="19-05-2027 00:00:00" value="0.65"/></basal>
    <temp_basal><event ts_begin="19-05-2027 01:00:00" ts_end="19-05-2027 01:05:00" value="0"/></temp_basal>
    <bolus><event ts_begin="19-05-2027 01:00:00" ts_end="19-05-2027 01:00:00" dose="1.6" type="normal"/></bolus>
    <meal><event ts="19-05-2027 01:00:00" carbs="25" type="Snack"/></meal></patient>'''
    path.write_text(xml)
    duplicate = tmp_path/'559-test.xml'
    duplicate.write_text(xml)
    data = parse_files([path,duplicate],tmp_path)
    assert len(data['glucose']) == 2
    assert len(data['events']) == 4
    assert data['start'].year == 2027
    assert data['rejected'] == []
    by_kind = {row['kind']:row for row in data['events'].values()}
    assert (by_kind['bolus']['value'],by_kind['bolus']['unit']) == (1.6,'U')
    assert (by_kind['basal']['value'],by_kind['basal']['unit']) == (0.65,'U/h')
    assert by_kind['temp_basal']['value'] == 0
    assert by_kind['meal']['unit'] == 'g'
    path.write_text(xml.replace('value="101"','value="103"'))
    with pytest.raises(RuntimeError,match='Conflicting CGM'):
        parse_files([path,duplicate],tmp_path)


async def account(client, session, role=UserRole.PATIENT):
    username='study_'+uuid4().hex[:12]
    response=await client.post('/api/v1/auth/register',json={'username':username,'password':'study-test-password','consent_accepted':True})
    assert response.status_code == 201
    user=await session.scalar(select(User).where(User.username==username))
    assert user is not None
    if role != UserRole.PATIENT:
        await set_user_role(session, username=username, role=role, maintainer='test')
    return user,{'Authorization':'Bearer '+response.json()['access_token']}


async def test_study_timeline_obeys_patient_access_and_keeps_carry_in_basal(client,db_session):
    patient,ph=await account(client,db_session)
    doctor,dh=await account(client,db_session,UserRole.CLINICIAN)
    begin=datetime(2027,5,19,tzinfo=timezone.utc)
    db_session.add(StudySubject(user_id=patient.id,dataset='OhioT1DM',insulin_type='Humalog',period_start=begin,period_end=begin+timedelta(days=2),provenance={'notice':'research'}))
    db_session.add(StudyEvent(id=uuid4(),user_id=patient.id,kind='basal',measured_at=begin-timedelta(hours=2),ended_at=begin+timedelta(hours=2),value=0.65,unit='U/h',detail='',source_ref='test.xml:basal:1',raw_record={}))
    await db_session.flush()
    path=f'/api/v1/clinical/patients/{patient.id}/overview'
    params={'start':begin.isoformat(),'end':(begin+timedelta(days=1)).isoformat()}
    assert (await client.get(path,headers=dh,params=params)).status_code == 403
    assert (await client.post('/api/v1/patient-access-grants',headers=ph,json={'clinician_id':str(doctor.id)})).status_code == 201
    response=await client.get(path,headers=dh,params=params)
    assert response.status_code == 200, response.text
    study=response.json()['study']
    assert study['insulin_type'] == 'Humalog'
    assert study['events'][0]['value'] == 0.65
    assert study['events'][0]['unit'] == 'U/h'
    assert 'raw_record' not in study['events'][0]
