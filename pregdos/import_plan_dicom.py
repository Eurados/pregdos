import copy
import logging
import numpy as np
from pathlib import Path

from pregdos.model_plan import Plan, Field, Layer, Spot, RangeShifter

logger = logging.getLogger(__name__)


def load_plan_dicom(file_dcm: Path) -> Plan:
    """Load DICOM RTPLAN."""

    p = Plan()
    try:
        import pydicom as dicom

    except ImportError:
        logger.error("pydicom is not installed, cannot read DICOM files.")
        logger.error("Please install pymchelper[dicom] or pymchelper[all] to us this feature.")
        return p
    d = dicom.dcmread(file_dcm)

    # Check if the file is an RTPLAN
    if d.Modality != "RTPLAN":
        logger.error("File %s is not a valid RTPLAN file (Modality: %s)", file_dcm, d.Modality)
        raise ValueError("File is not a valid RTPLAN DICOM file.")

    # Check also the SOP Class UID
    if d.SOPClassUID != '1.2.840.10008.5.1.4.1.1.481.8':
        logger.error("File %s is not a valid RTPLAN file (SOP Class UID: %s)", file_dcm, d.SOPClassUID)
        raise ValueError("File is not a valid RTPLAN DICOM file.")

    # optional attributes that may not be present in all RTPLAN files and can default to empty strings
    p.patient_id = getattr(d, 'PatientID', '')
    p.patient_name = getattr(d, 'PatientName', '')
    p.patient_initials = getattr(d, 'PatientInitials', '')
    p.patient_firstname = getattr(d, 'PatientFirstName', '')
    p.plan_label = getattr(d, 'RTPlanLabel', '')
    p.plan_date = getattr(d, 'RTPlanDate', '')

    # mandatory attributes, code will raise an error if they are not present
    p.sop_instance_uid = d['SOPInstanceUID'].value

    espread = 0.0  # will be set by beam model
    n_fields = int(d['FractionGroupSequence'][0]['NumberOfBeams'].value)
    logger.debug("Found %i fields", n_fields)

    rbs = d['FractionGroupSequence'][0]['ReferencedBeamSequence']  # fields for given group number
    for i, rb in enumerate(rbs):
        myfield = Field()
        field_nr = i + 1
        myfield.field_number = field_nr
        logger.debug("Appending field number %d...", field_nr)
        p.fields.append(myfield)
        myfield.sop_instance_uid = p.sop_instance_uid
        myfield.dose = float(rb['BeamDose'].value)
        myfield.cum_mu = float(rb['BeamMeterset'].value)

    ibs = d['IonBeamSequence']  # ion beam sequence, contains all fields
    if len(ibs.value) != n_fields:
        logger.error("Number of fields in IonBeamSequence (%d) does not match FractionGroupSequence (%d).",
                     len(ibs.value), n_fields)
        raise ValueError("Inconsistent number of fields in DICOM plan.")

    for i, ib in enumerate(ibs):
        myfield = p.fields[i]
        field_nr = i + 1
        n_layers = int(ib['NumberOfControlPoints'].value) // 2  # each layer has 2 control points
        myfield.meterset_weight_final = float(ib['FinalCumulativeMetersetWeight'].value)
        myfield.meterset_per_weight = myfield.cum_mu / myfield.meterset_weight_final

        icps = ib['IonControlPointSequence']  # layers for given field number
        logger.debug("Found %i layers in field number %i", n_layers, field_nr)

        cmu = 0.0

        # If range shifters are present, build the RS lookup dictionary
        logger.debug("Checking for Range Shifter Sequence in field number %i", field_nr)
        if 'RangeShifterSequence' in ib:
            rs_dict = {}
            for rs_item in ib['RangeShifterSequence']:
                rs = RangeShifter()
                if 'RangeShifterNumber' in rs_item:
                    rs.number = int(rs_item['RangeShifterNumber'].value)  # Use DICOM tag value
                else:
                    logger.error("RangeShifterNumber not found in DICOM plan.")
                    continue
                if 'RangeShifterID' in rs_item:
                    rs.type = rs_item['RangeShifterType'].value
                    rs.id = rs_item['RangeShifterID'].value
                    logger.debug("Found range shifter ID: %s", rs.id)
                    if rs.id == 'None':
                        rs.thickness = 0.0
                    elif rs.id == 'RS_3CM':
                        rs.thickness = 30.0
                    elif rs.id == 'RS_5CM':
                        rs.thickness = 50.0
                else:
                    logger.error("Unknown range shifter ID in DICOM plan.")
                logger.debug("Found range shifter number %d with type %s and thickness %.2f mm",
                             rs.number, rs.type, rs.thickness)
                rs_dict[rs.number] = rs  # Store by DICOM number for lookup

        for j, icp in enumerate(icps):
            layer_nr = j + 1
            # Several attributes are only set once at the first ion control point.
            # The strategy here is then to still set them for every layer, even if they do not change.
            # This is to ensure that the field object has all necessary attributes set.
            # But also enables future stuff like arc therapy, where these values may change per layer.
            if 'LateralSpreadingDeviceSettingsSequence' in icp:
                if len(icp['LateralSpreadingDeviceSettingsSequence'].value) != 2:
                    logger.error("LateralSpreadingDeviceSettingsSequence should contain exactly 2 elements, found %d.",
                                 len(ib['LateralSpreadingDeviceSettingsSequence'].value))
                    raise ValueError("Invalid LateralSpreadingDeviceSettingsSequence in DICOM plan.")

                lss = icp['LateralSpreadingDeviceSettingsSequence']
                sad_x = float(lss[0]['IsocenterToLateralSpreadingDeviceDistance'].value)
                sad_y = float(lss[1]['IsocenterToLateralSpreadingDeviceDistance'].value)

                logger.debug("Set Lateral spreading device distances: X = %.2f mm, Y = %.2f mm",
                             sad_x, sad_y)

            # check snout position
            if 'SnoutPosition' in icp:
                snout_position = float(icp['SnoutPosition'].value)

            if 'RangeShifterSettingsSequence' in icp:
                for rss in icp['RangeShifterSettingsSequence']:
                    if getattr(rss, 'RangeShifterSetting', None) == "IN":
                        # lookup range shifter by number, and make a copy of it
                        _rs_number = rss['ReferencedRangeShifterNumber'].value
                        _rs = rs_dict[_rs_number]
                        myfield.range_shifter = copy.deepcopy(_rs)
                        # set remaining attributes
                        myfield.range_shifter.has_range_shifter = True
                        myfield.range_shifter.water_equivalent_thickness = rss.get('WaterEquivalentThickness', 0.0)
                        myfield.range_shifter.isocenter_distance = rss.get('IsocenterToRangeShifterDistance', 0.0)

            # isocenter position and gantry counch angles are stored in each layer,
            # for now we assume they are the same for all layers in a field,
            # ideally these attributes should be stored in the layer object
            # then conversion can change it to a field level for topas export.
            if 'IsocenterPosition' in icp:
                isocenter = tuple(float(v) for v in icp['IsocenterPosition'].value)
            if 'GantryAngle' in icp:
                gantry_angle = float(icp['GantryAngle'].value)
            if 'PatientSupportAngle' in icp:
                couch_angle = float(icp['PatientSupportAngle'].value)

            # Check each required DICOM tag individually
            if 'NominalBeamEnergy' in icp:
                energy = float(icp['NominalBeamEnergy'].value)  # Nominal energy in MeV

            if 'NumberOfScanSpotPositions' in icp:
                nspots = int(icp['NumberOfScanSpotPositions'].value)  # number of spots

            if 'ScanSpotPositionMap' in icp:  # Extract spot MU and scale [MU]
                pos = np.array(icp['ScanSpotPositionMap'].value).reshape(nspots, 2)

            if 'ScanSpotMetersetWeights' in icp:
                mu = np.array(icp['ScanSpotMetersetWeights'].value).reshape(nspots) * myfield.meterset_per_weight

            if 'ScanningSpotSize' in icp:             # Extract spot nominal sizes [mm FWHM]
                size_x, size_y = icp['ScanningSpotSize'].value

            logger.debug("Found %i spots in layer number %i at energy %f", nspots, layer_nr, energy)
            nrepaint = int(icp['NumberOfPaintings'].value)  # number of spots

            spots = [Spot(x=x, y=y, mu=mu_val, size_x=size_x, size_y=size_y)
                     for (x, y), mu_val in zip(pos, mu)]

            # only append layer, if sum of mu are larger than 0
            sum_mu = np.sum(mu)

            if sum_mu > 0.0:
                cmu += sum_mu
                myfield.layers.append(Layer(
                    spots=spots,
                    energy_nominal=energy,
                    energy_measured=energy,
                    espread=espread,
                    cum_mu=cmu,
                    repaint=nrepaint,
                    mu_to_part_coef=0.0,
                    isocenter=isocenter,
                    gantry_angle=gantry_angle,
                    couch_angle=couch_angle,
                    snout_position=snout_position,
                    sad=(sad_x, sad_y)
                ))
            else:
                logger.debug("Skipping empty layer %i", j)
    return p
