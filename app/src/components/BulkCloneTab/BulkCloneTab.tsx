import { Download, FileJson, Loader2, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { LANGUAGE_OPTIONS } from '@/lib/constants/languages';
import { useBatchClone } from '@/lib/hooks/useBatchClone';

const STT_MODELS = [
  { value: 'tiny', label: 'Tiny' },
  { value: 'base', label: 'Base' },
  { value: 'small', label: 'Small' },
  { value: 'medium', label: 'Medium' },
  { value: 'large', label: 'Large' },
];

const AUDIO_EXTENSIONS = '.wav,.mp3,.flac,.ogg,.m4a';

export function BulkCloneTab() {
  const { toast } = useToast();
  const {
    startBatchClone,
    isStarting,
    batchId,
    status,
    progress,
    error,
    downloadZipUrl,
    reset,
  } = useBatchClone();

  const [mode, setMode] = useState<'youtube' | 'upload'>('youtube');
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [textLines, setTextLines] = useState('');
  const [language, setLanguage] = useState('en');
  const [sttModel, setSttModel] = useState('base');
  const [audioFileCount, setAudioFileCount] = useState(0);
  const [hasTextFile, setHasTextFile] = useState(false);
  const [textInputMode, setTextInputMode] = useState<'type' | 'txt' | 'json'>('type');
  const [hasJsonFile, setHasJsonFile] = useState(false);
  const [jsonFile, setJsonFile] = useState<File | null>(null);

  const textFileRef = useRef<HTMLInputElement>(null);
  const jsonFileRef = useRef<HTMLInputElement>(null);
  const audioFilesRef = useRef<HTMLInputElement>(null);

  const EXAMPLE_JSON = `{
  "categories": {
    "greetings": {
      "wavs": [
        { "wav": "hello.wav", "text": "hello" },
        { "wav": "yes.wav", "text": "yes" },
        { "wav": "okay.wav", "text": "okay" }
      ]
    }
  }
}`;

  const handleRun = async () => {
    if (textInputMode === 'json') {
      if (!jsonFile || !hasJsonFile) {
        toast({
          title: 'JSON file required',
          description: 'Upload a JSON file matching the schema.',
          variant: 'destructive',
        });
        return;
      }
    } else {
      const lines = textLines
        .trim()
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean);
      if (lines.length === 0) {
        toast({
          title: 'Text required',
          description: 'Enter at least one phrase per line, or upload a .txt or .json file.',
          variant: 'destructive',
        });
        return;
      }
    }

    const formData = new FormData();
    formData.append('mode', mode);
    formData.append('language', language);
    formData.append('stt_model', sttModel);
    formData.append('text_input_mode', textInputMode);

    if (textInputMode === 'json' && jsonFile && hasJsonFile) {
      formData.append('json_file', jsonFile);
    } else if (textInputMode === 'txt' && textFileRef.current?.files?.[0] && hasTextFile) {
      formData.append('text_file', textFileRef.current.files[0]);
    } else {
      const lines = textLines
        .trim()
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean);
      formData.append('text', lines.join('\n'));
    }

    if (mode === 'youtube') {
      if (!youtubeUrl.trim()) {
        toast({
          title: 'YouTube URL required',
          description: 'Paste the YouTube video URL.',
          variant: 'destructive',
        });
        return;
      }
      if (!startTime.trim() || !endTime.trim()) {
        toast({
          title: 'Timestamps required',
          description: 'Enter start and end times (e.g. 0 and 30, or 1:30).',
          variant: 'destructive',
        });
        return;
      }
      formData.append('youtube_url', youtubeUrl.trim());
      formData.append('start_seconds', startTime.trim());
      formData.append('end_seconds', endTime.trim());
    } else {
      const files = audioFilesRef.current?.files;
      if (!files || files.length === 0) {
        toast({
          title: 'Audio files required',
          description: 'Upload at least one audio file.',
          variant: 'destructive',
        });
        return;
      }
      for (let i = 0; i < files.length; i++) {
        formData.append('audio_files', files[i]);
      }
    }

    try {
      await startBatchClone(formData);
      toast({
        title: 'Batch started',
        description: 'Processing in progress. You can monitor progress below.',
      });
    } catch (err) {
      toast({
        title: 'Failed to start batch',
        description: err instanceof Error ? err.message : 'Unknown error',
        variant: 'destructive',
      });
    }
  };

  const handleTextFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    setHasTextFile(!!file);
    if (file) {
      const reader = new FileReader();
      reader.onload = () => {
        setTextLines((reader.result as string) || '');
      };
      reader.readAsText(file);
    }
  };

  const handleJsonFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    setHasJsonFile(!!file);
    setJsonFile(file);
  };

  const handleDownloadZip = () => {
    if (downloadZipUrl) {
      window.open(downloadZipUrl, '_blank');
    }
  };

  const hasText =
    textInputMode === 'json'
      ? hasJsonFile
      : textLines.trim().split('\n').filter(Boolean).length > 0 || hasTextFile;
  const canRun =
    !isStarting &&
    !batchId &&
    ((mode === 'youtube' && youtubeUrl.trim() && startTime.trim() && endTime.trim()) ||
      (mode === 'upload' && audioFileCount > 0)) &&
    hasText;

  return (
    <div className="flex flex-col h-full overflow-auto">
      <div className="py-6">
        <h1 className="text-2xl font-semibold mb-2">Bulk Voice Clone</h1>
        <p className="text-muted-foreground mb-6">
          Clone voices from YouTube or uploaded audio, then generate speech for multiple phrases. Each line
          produces a separate output file. A ZIP with all files is provided when complete.
        </p>

        <Tabs value={mode} onValueChange={(v) => setMode(v as 'youtube' | 'upload')} className="space-y-4">
          <TabsList>
            <TabsTrigger value="youtube">From YouTube</TabsTrigger>
            <TabsTrigger value="upload">From Upload</TabsTrigger>
          </TabsList>

          <TabsContent value="youtube" className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="youtube-url">YouTube URL</Label>
              <Input
                id="youtube-url"
                placeholder="https://www.youtube.com/watch?v=..."
                value={youtubeUrl}
                onChange={(e) => setYoutubeUrl(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="start-time">Start (seconds or M:SS)</Label>
                <Input
                  id="start-time"
                  placeholder="0 or 1:30"
                  value={startTime}
                  onChange={(e) => setStartTime(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="end-time">End (seconds or M:SS)</Label>
                <Input
                  id="end-time"
                  placeholder="30 or 2:00"
                  value={endTime}
                  onChange={(e) => setEndTime(e.target.value)}
                />
              </div>
            </div>
          </TabsContent>

          <TabsContent value="upload" className="space-y-4">
            <div className="space-y-2">
              <Label>Audio files</Label>
              <div className="flex items-center gap-2">
                <Input
                  ref={audioFilesRef}
                  type="file"
                  accept={AUDIO_EXTENSIONS}
                  multiple
                  className="max-w-sm"
                  onChange={(e) => setAudioFileCount(e.target.files?.length ?? 0)}
                />
                <span className="text-sm text-muted-foreground">
                  .wav, .mp3, .flac, .ogg, .m4a (2–30 sec each)
                </span>
              </div>
            </div>
          </TabsContent>
        </Tabs>

        <div className="mt-6 space-y-4">
          <div className="space-y-3">
            <Label>Text for TTS</Label>
            <Tabs
              value={textInputMode}
              onValueChange={(v) => setTextInputMode(v as 'type' | 'txt' | 'json')}
              className="w-full"
            >
              <TabsList className="grid w-full max-w-md grid-cols-3">
                <TabsTrigger value="type">Type / Paste</TabsTrigger>
                <TabsTrigger value="txt">Upload TXT</TabsTrigger>
                <TabsTrigger value="json">Upload JSON</TabsTrigger>
              </TabsList>
              <TabsContent value="type" className="mt-3 space-y-2">
                <Textarea
                  id="text-lines"
                  placeholder="What is your name?&#10;Where are you from?&#10;What do you do for a living?"
                  rows={5}
                  value={textLines}
                  onChange={(e) => setTextLines(e.target.value)}
                />
                <p className="text-sm text-muted-foreground">
                  Each line becomes a separate output audio file per source voice.
                </p>
              </TabsContent>
              <TabsContent value="txt" className="mt-3 space-y-2">
                <Input
                  ref={textFileRef}
                  type="file"
                  accept=".txt"
                  onChange={handleTextFileChange}
                  className="max-w-xs"
                />
                <p className="text-sm text-muted-foreground">
                  Upload a .txt file with one phrase per line.
                </p>
              </TabsContent>
              <TabsContent value="json" className="mt-3 space-y-3">
                <div className="space-y-2">
                  <Input
                    ref={jsonFileRef}
                    type="file"
                    accept=".json,application/json"
                    onChange={handleJsonFileChange}
                    className="max-w-xs"
                  />
                  <p className="text-sm text-muted-foreground">
                    Upload a JSON file matching the schema below. The <code className="rounded bg-muted px-1 py-0.5 text-xs">wav</code> key is the output filename; <code className="rounded bg-muted px-1 py-0.5 text-xs">text</code> is sent to TTS.
                  </p>
                </div>
                <Card className="border-muted bg-muted/30">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium flex items-center gap-2">
                      <FileJson className="h-4 w-4" />
                      Example JSON schema
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="pt-0">
                    <pre className="overflow-x-auto rounded-md border bg-background p-4 text-xs">
                      <code className="text-foreground">{EXAMPLE_JSON}</code>
                    </pre>
                  </CardContent>
                </Card>
              </TabsContent>
            </Tabs>
          </div>

          <div className="flex flex-wrap gap-4">
            <div className="space-y-2">
              <Label>Language</Label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger className="w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LANGUAGE_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Whisper model</Label>
              <Select value={sttModel} onValueChange={setSttModel}>
                <SelectTrigger className="w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STT_MODELS.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {!batchId ? (
            <Button onClick={handleRun} disabled={!canRun}>
              {isStarting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Starting...
                </>
              ) : (
                <>
                  <Upload className="mr-2 h-4 w-4" />
                  Run batch clone
                </>
              )}
            </Button>
          ) : (
            <div className="space-y-4 rounded-lg border p-4">
              {status === 'processing' && (
                <div className="flex items-center gap-2">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  <span>
                    Processing source {progress?.current_source ?? 0} / {progress?.total_sources ?? 1},
                    line {progress?.current_line ?? 0} / {progress?.total_lines ?? 0}
                  </span>
                </div>
              )}
              {status === 'error' && (
                <div className="text-destructive">
                  <p className="font-medium">Error</p>
                  <p>{error}</p>
                  <Button variant="outline" size="sm" className="mt-2" onClick={reset}>
                    Try again
                  </Button>
                </div>
              )}
              {status === 'complete' && (
                <div className="space-y-2">
                  <p className="text-green-600 dark:text-green-400 font-medium">Complete!</p>
                  <Button onClick={handleDownloadZip}>
                    <Download className="mr-2 h-4 w-4" />
                    Download ZIP
                  </Button>
                  <Button variant="outline" size="sm" className="ml-2" onClick={reset}>
                    New batch
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
