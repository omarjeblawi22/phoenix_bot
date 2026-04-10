This data set will contain the following tests:
NLOS Tests:
1. Around the Corner (AC):
	Place the transmitter in an empty room in a corridor system, test for whether you're getting LOS or fractured data. 
	Test at 1m, 2m, 5m, 7.5m
	Horizontal difference of approximately 0.6m, small angle approximation does not work.
2. Through Wall {180 degrees} (TW):
	Place transmitter in a room and go directly opposite it
	Test at 1m, 2m, 5m, 7.5m
	Distances measured **from wall**, Add the following to above to get ground truth: wall thickness of 30cm and the AP-wall distance of 10cm.
3. Faraday Cage (FC):
	Place transmitter in a semi-faraday cage.
	The test was done at 1.25m away, the duration is 1 min 10 sec, the transmitter was placed inside the FC at 10 seconds into the recording. 


